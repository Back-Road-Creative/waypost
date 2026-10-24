"""Metricool PDF analytics import.

Parses Metricool monthly PDF reports (pdftotext -layout output) and creates
PERFORMANCE_UPDATED events in the JSONL event store, closing the feedback loop
between content generation and performance tracking.

Supports: TikTok, Facebook, Instagram, Threads, Twitter, Pinterest, YouTube.
No new Python dependencies — uses pdftotext (poppler) and stdlib difflib.

A report names each network section by the account's display name, and one
display name can be shared by several networks. Which display name belongs to
which network is therefore configuration, passed in as an ``accounts`` mapping
(see ``ACCOUNT_ROLES`` and ``load_accounts``); nothing is assumed about any
particular brand.
"""

import difflib
import logging
import re
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

import yaml

from .. import paths
from ..core.events import EventType, SEOEvent
from ..storage.event_store import SEOEventStore
from .metricool_import import _safe_float, _safe_int

logger = logging.getLogger(__name__)

# What an account display name stands for. Several networks can print the same
# display name, so a role names the group and the parser tells its members apart
# from the section's content:
#   facebook_youtube   page/channel name shared by Facebook and YouTube
#                      (YouTube when the section shows subscribers or watch time)
#   instagram_threads  handle shared by Instagram and Threads
#                      (the first community section is Instagram)
#   twitter, tiktok, pinterest   one network each
ACCOUNT_ROLES = (
    "facebook_youtube",
    "instagram_threads",
    "twitter",
    "tiktok",
    "pinterest",
)


def load_accounts(path) -> Dict[str, str]:
    """Read an ``{account display name: role}`` mapping from a YAML/JSON file."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a mapping of account name to role")
    return validate_accounts(data)


def validate_accounts(accounts: Mapping[str, str]) -> Dict[str, str]:
    """Return ``accounts`` as a plain dict, rejecting an unknown role."""
    bad = sorted({str(r) for r in accounts.values() if r not in ACCOUNT_ROLES})
    if bad:
        raise ValueError(
            f"unknown account role(s) {bad}; expected one of {list(ACCOUNT_ROLES)}"
        )
    return {str(name): str(role) for name, role in accounts.items()}


# Date pattern in Metricool PDF (e.g., "Jan 8, 2026")
DATE_PATTERN = re.compile(
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2}),\s+(\d{4})"
)

# Time pattern (e.g., "5:04 PM")
TIME_PATTERN = re.compile(r"(\d{1,2}:\d{2})\s*(AM|PM)", re.IGNORECASE)

MONTH_MAP = {
    "Jan": "01",
    "Feb": "02",
    "Mar": "03",
    "Apr": "04",
    "May": "05",
    "Jun": "06",
    "Jul": "07",
    "Aug": "08",
    "Sep": "09",
    "Oct": "10",
    "Nov": "11",
    "Dec": "12",
}

# Duration pattern (e.g., "15s", "2h 6m 0s", "1m 30s")
DURATION_PATTERN = re.compile(r"^(?:\d+h\s*)?(?:\d+m\s*)?(?:\d+s)$")

# Metric-like token: digits with optional k/K/m/M suffix, commas, decimals, percent
METRIC_TOKEN_PATTERN = re.compile(r"^[\d,.]+[kKmM%]?$")


def _parse_metric_value(val: str):
    """Parse a metric value from PDF text.

    Handles k/m suffixes, durations, dashes, commas.
    Returns int, float, or string (for durations).
    """
    if not val:
        return 0
    val = val.strip()
    if val == "-":
        return 0

    # Duration strings (e.g., "2h 6m 0s", "15s")
    if DURATION_PATTERN.match(val):
        return val  # Keep as string

    # k/K suffix (e.g., "98.76k" -> 98760)
    lower = val.lower()
    if lower.endswith("k"):
        try:
            return int(float(lower[:-1]) * 1000)
        except ValueError:
            return 0
    if lower.endswith("m") and not lower.endswith("am") and not lower.endswith("pm"):
        try:
            return int(float(lower[:-1]) * 1_000_000)
        except ValueError:
            return 0

    # Percentage
    if val.endswith("%"):
        return _safe_float(val)

    # Try int first, then float
    clean = val.replace(",", "")
    try:
        return int(clean)
    except ValueError:
        try:
            return float(clean)
        except ValueError:
            return 0


@dataclass
class ExtractedPost:
    """A post extracted from the PDF."""

    platform: str  # "tiktok", "instagram", "youtube", etc.
    published_date: str  # "2025-03-08"
    published_time: str  # "17:04" or ""
    caption_snippet: str  # First ~100 chars of caption
    metrics: Dict[str, float] = field(default_factory=dict)
    row_index: int = 0


class PDFTextExtractor:
    """Extract text from PDF using pdftotext."""

    def extract(self, pdf_path: str, layout: bool = True) -> str:
        """Run pdftotext and return stdout."""
        pdf_path = str(pdf_path)
        if not Path(pdf_path).exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        cmd = ["pdftotext"]
        if layout:
            cmd.append("-layout")
        cmd.extend([pdf_path, "-"])

        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True, timeout=60
        )
        return result.stdout

    def extract_cached(self, pdf_path: str) -> str:
        """Extract with sidecar .extracted.txt cache.

        If pdf_path points to a .txt or .extracted.txt file directly,
        reads it as pre-extracted text (no pdftotext needed).
        """
        pdf_path = Path(pdf_path)

        # Accept pre-extracted text files directly
        if pdf_path.suffix in (".txt",):
            return pdf_path.read_text(encoding="utf-8")

        cache_path = pdf_path.with_suffix(".extracted.txt")

        if cache_path.exists():
            pdf_mtime = pdf_path.stat().st_mtime
            cache_mtime = cache_path.stat().st_mtime
            if cache_mtime >= pdf_mtime:
                return cache_path.read_text(encoding="utf-8")

        text = self.extract(str(pdf_path))
        cache_path.write_text(text, encoding="utf-8")
        return text


# Known column names ordered by specificity (longer first for matching)
KNOWN_COLUMNS = [
    "Link clicks",
    "Pin clicks",
    "Outbound clicks",
    "Profile clicks",
    "Video views",
    "Avg. time watched",
    "Time watched",
    "Watch time",
    "Average views",
    "Engagement Rate",
    "Views",
    "Likes",
    "Comments",
    "Shares",
    "Reach",
    "Duration",
    "Engagement",
    "Reactions",
    "Clicks",
    "Impressions",
    "Saves",
    "Actions",
    "Replies",
    "Reposts",
    "Quotes",
    "Dislikes",
]

# Section boundary keywords
SECTION_BOUNDARIES = (
    "Community growth",
    "Ranking of",
    "Showing",
    "Demographics",
    "Best time",
    "Content formats",
    "Evolution",
    "New followers",
)


class MetricoolPDFParser:
    """State machine parser for Metricool PDF layout text."""

    SCANNING = "scanning"
    EXPECT_DATE = "expect_date"
    EXPECT_CONTENT = "expect_content"

    def __init__(self, accounts: Optional[Mapping[str, str]] = None):
        # display name -> role (see ACCOUNT_ROLES); empty means nothing is
        # recognised, so no section is attributed to a network
        self.accounts: Dict[str, str] = validate_accounts(accounts or {})
        self.posts: List[ExtractedPost] = []
        self.community: Dict[str, Dict] = {}
        self.demographics: Dict[str, Dict] = {}
        self.report_period: Dict[str, str] = {}

    def parse(self, text: str) -> dict:
        """Parse the full pdftotext layout output."""
        lines = text.split("\n")

        state = self.SCANNING
        current_platform = None
        column_headers: List[Tuple[str, int]] = []
        current_date = ""
        current_time = ""
        caption_lines: List[str] = []
        row_index = 0

        # Track platform section occurrences for disambiguation
        shared_page_count = 0
        shared_handle_count = 0

        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            # --- Detect community growth sections ---
            if "Community growth" in stripped:
                platform = self._detect_community_platform(
                    lines, i, shared_page_count, shared_handle_count
                )
                if platform:
                    current_platform = platform
                    if platform in ("facebook", "youtube"):
                        shared_page_count += 1
                    elif platform in ("instagram", "threads"):
                        shared_handle_count += 1
                    self._parse_community_growth(lines, i, platform)
                i += 1
                continue

            # --- Detect ranking tables ---
            if stripped.startswith("Ranking of"):
                ranking_type = stripped.lower()
                platform, headers, skip_to = self._parse_ranking_header(
                    lines, i, ranking_type, shared_page_count, shared_handle_count
                )
                if platform:
                    current_platform = platform
                if headers:
                    column_headers = headers
                    state = self.EXPECT_DATE
                    row_index = 0
                    caption_lines = []
                    current_date = ""
                    current_time = ""
                    i = skip_to
                    continue
                i += 1
                continue

            # --- State machine for post extraction ---
            if state == self.EXPECT_DATE:
                date_match = DATE_PATTERN.search(stripped)
                if date_match:
                    month = MONTH_MAP[date_match.group(1)]
                    day = date_match.group(2).zfill(2)
                    year = date_match.group(3)
                    current_date = f"{year}-{month}-{day}"
                    current_time = ""
                    caption_lines = []
                    state = self.EXPECT_CONTENT

                    # Capture caption text from the line before the date
                    if i > 0:
                        prev = lines[i - 1].strip()
                        if (
                            prev
                            and not DATE_PATTERN.search(prev)
                            and not TIME_PATTERN.search(prev)
                            and not self._is_section_boundary(prev)
                        ):
                            caption_lines.append(prev)
                elif stripped and self._is_section_boundary(stripped):
                    state = self.SCANNING

            elif state == self.EXPECT_CONTENT:
                # Check for time-only line
                time_match = TIME_PATTERN.search(stripped)
                if time_match and len(stripped) < 15:
                    current_time = self._parse_time(time_match)
                    i += 1
                    continue

                # Check for metrics line
                if self._has_metrics(line, column_headers):
                    metrics = self._extract_metrics(line, column_headers)
                    text_part = self._extract_text_part(line, column_headers)
                    if text_part:
                        caption_lines.append(text_part)

                    caption = " ".join(caption_lines).strip()[:100]
                    if current_platform and current_date:
                        self.posts.append(
                            ExtractedPost(
                                platform=current_platform,
                                published_date=current_date,
                                published_time=current_time,
                                caption_snippet=caption,
                                metrics=metrics,
                                row_index=row_index,
                            )
                        )
                        row_index += 1

                    caption_lines = []
                    current_time = ""
                    state = self.EXPECT_DATE

                elif self._is_section_boundary(stripped):
                    state = self.SCANNING

                elif stripped:
                    # Check if this is a new date (next post started)
                    date_match = DATE_PATTERN.search(stripped)
                    if date_match:
                        # New post date — previous post had no metrics, skip it
                        month = MONTH_MAP[date_match.group(1)]
                        day = date_match.group(2).zfill(2)
                        year = date_match.group(3)
                        current_date = f"{year}-{month}-{day}"
                        caption_lines = []
                        current_time = ""
                    else:
                        # Accumulate caption text
                        caption_lines.append(stripped)

            i += 1

        return {
            "posts": self.posts,
            "community": self.community,
            "demographics": self.demographics,
            "report_period": self.report_period,
        }

    def _detect_community_platform(
        self, lines, start_idx, shared_page_count, shared_handle_count
    ):
        """Detect platform from account name near Community growth header."""
        for j in range(start_idx + 1, min(start_idx + 5, len(lines))):
            acct = lines[j].strip()
            if not acct:
                continue
            context = "\n".join(lines[j : min(j + 15, len(lines))])
            role = self.accounts.get(acct)
            if role == "facebook_youtube":
                if "Subscribers" in context or "Revenue" in context:
                    return "youtube"
                return "facebook"
            if role == "instagram_threads":
                if shared_handle_count == 0:
                    return "instagram"
                return "threads"
            if role in ("twitter", "tiktok", "pinterest"):
                return role
        return None

    def _parse_ranking_header(
        self, lines, start_idx, ranking_type, shared_page_count, shared_handle_count
    ):
        """Parse ranking table header, detect platform and column positions.

        Returns: (platform, column_headers, skip_to_line)
        """
        platform = None
        column_headers = []
        skip_to = start_idx + 1

        for j in range(start_idx + 1, min(start_idx + 15, len(lines))):
            hline = lines[j].strip()
            if not hline:
                continue

            # Account name detection
            role = self.accounts.get(hline)
            if role:
                context = "\n".join(lines[j : min(j + 10, len(lines))])
                if role == "facebook_youtube":
                    if "Watch time" in context or "Subscribers" in context:
                        platform = "youtube"
                    elif "reels" in ranking_type:
                        platform = "facebook_reels"
                    else:
                        platform = "facebook"
                elif role == "instagram_threads":
                    if "reels" in ranking_type:
                        platform = "instagram_reels"
                    elif "Reposts" in context or "Quotes" in context:
                        platform = "threads"
                    else:
                        platform = "instagram"
                else:  # twitter, tiktok or pinterest: one network per role
                    platform = role

            # Column header line
            if hline.startswith("Published"):
                column_headers = self._parse_column_headers(lines[j])
                skip_to = j + 1
                break

        return platform, column_headers, skip_to

    def _parse_column_headers(self, header_line: str) -> List[Tuple[str, int]]:
        """Parse column header line to get (name, char_position) pairs."""
        found = []
        used_positions = set()

        for col in KNOWN_COLUMNS:
            # Find all occurrences, pick first unused
            start = 0
            while True:
                idx = header_line.find(col, start)
                if idx < 0:
                    break
                if idx not in used_positions:
                    # Check it's not a substring of an already-found longer column
                    overlap = False
                    for _, pos in found:
                        if pos <= idx < pos + len(_):
                            overlap = True
                            break
                    if not overlap:
                        found.append((col, idx))
                        used_positions.add(idx)
                        break
                start = idx + 1

        found.sort(key=lambda x: x[1])

        # Filter out non-metric columns
        return [
            (name, pos)
            for name, pos in found
            if name not in ("Published", "Type", "Text")
        ]

    def _has_metrics(self, line: str, column_headers: List[Tuple[str, int]]) -> bool:
        """Check if a line has numeric values at expected column positions."""
        if not column_headers:
            return False

        metric_count = 0
        for idx, (_name, pos) in enumerate(column_headers):
            if pos >= len(line):
                continue
            if idx + 1 < len(column_headers):
                end = column_headers[idx + 1][1]
            else:
                end = len(line)
            segment = line[pos:end].strip()
            tokens = segment.split()
            if tokens:
                token = tokens[0]
                if (
                    token == "-"
                    or METRIC_TOKEN_PATTERN.match(token)
                    or DURATION_PATTERN.match(token)
                ):
                    metric_count += 1

        return metric_count >= 2

    def _extract_metrics(
        self, line: str, column_headers: List[Tuple[str, int]]
    ) -> Dict[str, object]:
        """Extract metric values from a line using column positions."""
        metrics = {}

        for idx, (name, pos) in enumerate(column_headers):
            if idx + 1 < len(column_headers):
                end = column_headers[idx + 1][1]
            else:
                end = len(line)

            segment = line[pos:end].strip() if pos < len(line) else ""
            tokens = segment.split()

            key = name.lower().replace(" ", "_").replace(".", "")
            if tokens:
                val = _parse_metric_value(tokens[0])
                metrics[key] = val
            else:
                metrics[key] = 0

        return metrics

    def _extract_text_part(
        self, line: str, column_headers: List[Tuple[str, int]]
    ) -> str:
        """Extract the text/caption portion before the metrics."""
        if not column_headers:
            return ""
        first_metric_pos = column_headers[0][1]
        text = line[:first_metric_pos].strip()
        # Remove trailing "Go" marker artifact
        text = re.sub(r"\s+Go\s*$", "", text)
        return text

    def _parse_time(self, match) -> str:
        """Convert 12-hour time match to 24-hour format."""
        time_str = match.group(1)
        ampm = match.group(2).upper()
        hour, minute = time_str.split(":")
        hour = int(hour)
        if ampm == "PM" and hour != 12:
            hour += 12
        elif ampm == "AM" and hour == 12:
            hour = 0
        return f"{hour:02d}:{minute}"

    def _is_section_boundary(self, stripped: str) -> bool:
        """Check if a line marks a section boundary."""
        return any(stripped.startswith(b) for b in SECTION_BOUNDARIES)

    def _parse_community_growth(self, lines: List[str], start_idx: int, platform: str):
        """Parse community growth section for followers/gained/lost.

        Real Metricool PDFs place values ABOVE their labels:
          921                62         17         4       <- values
          +1.21%            +5.08%    -77.33%    -42.86%  <- percentages
          Followers    Total content   Acquired    Lost    <- labels

        YouTube splits Gained/Lost into a second group below.
        Also handles the test fixture layout (labels above values).
        """
        data = {"followers": 0, "gained": 0, "lost": 0}

        # Collect section lines until next boundary
        section_end = min(start_idx + 30, len(lines))
        for j in range(start_idx + 1, section_end):
            if self._is_section_boundary(lines[j].strip()):
                section_end = j
                break

        section = lines[start_idx:section_end]

        for i, raw_line in enumerate(section):
            stripped = raw_line.strip()
            if not stripped:
                continue

            # Labels line with Followers or Subscribers
            if re.search(r"\bFollowers\b", stripped) or re.search(
                r"\bSubscribers\b", stripped
            ):
                # Try values-above (real PDF) first, then values-below (fixture)
                values_idx = self._find_values_line_above(section, i)
                if values_idx is None:
                    values_idx = self._find_values_line_below(section, i)
                if values_idx is not None:
                    self._map_community_labels(section[values_idx], raw_line, data)

            # Separate Gained / Acquired line (YouTube pattern)
            if (
                re.search(r"\bGained\b", stripped)
                or re.search(r"\bAcquired\b", stripped)
            ) and not re.search(r"\bFollowers\b", stripped):
                values_idx = self._find_values_line_above(section, i)
                if values_idx is not None:
                    self._map_community_labels(section[values_idx], raw_line, data)

        if any(v > 0 for v in data.values()):
            self.community[platform] = data

    def _find_values_line_above(
        self, section: List[str], label_idx: int
    ) -> Optional[int]:
        """Find the nearest values line above a labels line, skipping % lines."""
        for k in range(label_idx - 1, -1, -1):
            stripped = section[k].strip()
            if not stripped:
                continue
            if "%" in stripped:
                continue
            if re.search(r"\d", stripped):
                return k
            break
        return None

    def _find_values_line_below(
        self, section: List[str], label_idx: int
    ) -> Optional[int]:
        """Find the nearest values line below a labels line."""
        for k in range(label_idx + 1, len(section)):
            stripped = section[k].strip()
            if not stripped:
                continue
            if re.search(r"\d", stripped):
                return k
            break
        return None

    @staticmethod
    def _map_community_labels(values_line: str, labels_line: str, data: dict):
        """Map label keywords to values by ordinal position."""
        nums = re.findall(r"[\d,]+", values_line)
        if not nums:
            return

        label_map = {
            "Followers": "followers",
            "Subscribers": "followers",
            "Acquired": "gained",
            "Gained": "gained",
            "Lost": "lost",
        }
        found_labels = []
        for keyword, target_field in label_map.items():
            pos = labels_line.find(keyword)
            if pos >= 0:
                found_labels.append((pos, target_field))

        # Include non-target labels so ordinal alignment stays correct
        for skip_kw in ("Total content", "Revenue", "Videos", "Posts"):
            pos = labels_line.find(skip_kw)
            if pos >= 0:
                found_labels.append((pos, None))

        found_labels.sort(key=lambda x: x[0])

        for idx, (_pos, target_field) in enumerate(found_labels):
            if target_field is not None and idx < len(nums):
                data[target_field] = _safe_int(nums[idx])


class ContentMatcher:
    """Match PDF posts to known content IDs using caption similarity."""

    def __init__(self, known_content: Optional[Dict[str, Dict]] = None):
        """
        Args:
            known_content: Dict of content_id -> {date, caption, platform}
        """
        self.known_content = known_content or {}

    @staticmethod
    def normalize_caption(text: str) -> str:
        """Normalize caption for matching: lowercase, strip emoji/punctuation."""
        text = re.sub(r"[^\w\s]", "", text, flags=re.UNICODE)
        return text.lower().strip()

    def match(self, post: ExtractedPost) -> Optional[str]:
        """Match a post to a known content_id.

        Pass 1: exact date + first 35 chars normalized caption.
        Pass 2: date + difflib ratio >= 0.6.
        """
        norm_caption = self.normalize_caption(post.caption_snippet)

        # Pass 1: exact prefix match
        for content_id, info in self.known_content.items():
            if info.get("date") != post.published_date:
                continue
            known_norm = self.normalize_caption(info.get("caption", ""))
            if norm_caption[:35] == known_norm[:35] and norm_caption[:35]:
                return content_id

        # Pass 2: fuzzy match
        for content_id, info in self.known_content.items():
            if info.get("date") != post.published_date:
                continue
            known_norm = self.normalize_caption(info.get("caption", ""))
            if not known_norm or not norm_caption:
                continue
            ratio = difflib.SequenceMatcher(None, norm_caption, known_norm).ratio()
            if ratio >= 0.6:
                return content_id

        return None


class MetricoolPDFImporter:
    """Orchestrates PDF parsing and event store ingestion."""

    def __init__(
        self,
        event_store: Optional[SEOEventStore] = None,
        content_matcher: Optional[ContentMatcher] = None,
        accounts: Optional[Mapping[str, str]] = None,
    ):
        """``accounts`` maps account display names to roles (``ACCOUNT_ROLES``)."""
        self.accounts = validate_accounts(accounts or {})
        self.event_store = event_store
        self.content_matcher = content_matcher or ContentMatcher()
        self.extractor = PDFTextExtractor()

    def import_pdf(
        self,
        pdf_path: str,
        site_id: str = "default",
        dry_run: bool = False,
    ) -> dict:
        """Import a Metricool PDF report.

        Args:
            pdf_path: Path to the PDF file
            site_id: Base site ID (platform appended automatically)
            dry_run: If True, parse but don't write to event store

        Returns:
            Dict with counts: {posts, community, demographics, skipped}
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        # Extract text
        text = self.extractor.extract_cached(str(pdf_path))

        # Parse
        if not self.accounts:
            raise ValueError(
                "no accounts configured: pass accounts={display name: role} "
                f"(roles: {', '.join(ACCOUNT_ROLES)})"
            )
        parser = MetricoolPDFParser(self.accounts)
        result = parser.parse(text)

        counts = {"posts": 0, "community": 0, "demographics": 0, "skipped": 0}

        if dry_run:
            counts["posts"] = len(result["posts"])
            counts["community"] = len(result["community"])
            counts["demographics"] = len(result["demographics"])
            logger.info(f"Dry run: {counts}")
            return counts

        if self.event_store is None:
            raise ValueError("Event store not configured")

        # Create PERFORMANCE_UPDATED events for posts
        seen = set()
        for post in result["posts"]:
            dedup_key = (
                f"{post.published_date}|{post.platform}|{post.caption_snippet[:50]}"
            )
            if dedup_key in seen:
                counts["skipped"] += 1
                continue

            if self._already_imported(site_id, post):
                counts["skipped"] += 1
                continue

            seen.add(dedup_key)

            content_id = self.content_matcher.match(post)
            if not content_id:
                content_id = (
                    f"metricool_pdf_{post.platform}_"
                    f"{post.published_date}_r{post.row_index}"
                )

            event = SEOEvent(
                event_id=str(uuid.uuid4()),
                event_type=EventType.PERFORMANCE_UPDATED,
                site_id=f"{site_id}_{post.platform}",
                content_id=content_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                data={
                    **post.metrics,
                    "source": "metricool_pdf",
                    "source_date": post.published_date,
                    "published_time": post.published_time,
                    "caption_snippet": post.caption_snippet,
                },
            )
            self.event_store.append_event(event)
            counts["posts"] += 1

        # Create COMMUNITY_UPDATED events
        for platform, data in result["community"].items():
            event = SEOEvent(
                event_id=str(uuid.uuid4()),
                event_type=EventType.COMMUNITY_UPDATED,
                site_id=f"{site_id}_{platform}",
                content_id=f"community_{platform}",
                timestamp=datetime.now(timezone.utc).isoformat(),
                data={**data, "source": "metricool_pdf"},
            )
            self.event_store.append_event(event)
            counts["community"] += 1

        # Create DEMOGRAPHICS_UPDATED events
        for platform, data in result["demographics"].items():
            event = SEOEvent(
                event_id=str(uuid.uuid4()),
                event_type=EventType.DEMOGRAPHICS_UPDATED,
                site_id=f"{site_id}_{platform}",
                content_id=f"demographics_{platform}",
                timestamp=datetime.now(timezone.utc).isoformat(),
                data={**data, "source": "metricool_pdf"},
            )
            self.event_store.append_event(event)
            counts["demographics"] += 1

        logger.info(
            f"Imported from {pdf_path.name}: "
            f"{counts['posts']} posts, {counts['community']} community, "
            f"{counts['demographics']} demographics, {counts['skipped']} skipped"
        )
        return counts

    def _already_imported(self, site_id: str, post: ExtractedPost) -> bool:
        """Check if this post was already imported."""
        try:
            events = self.event_store.query_by_site(
                site_id=f"{site_id}_{post.platform}",
                event_type=EventType.PERFORMANCE_UPDATED,
            )
            for event in events:
                if (
                    event.data.get("source") == "metricool_pdf"
                    and event.data.get("source_date") == post.published_date
                    and event.data.get("caption_snippet", "")[:50]
                    == post.caption_snippet[:50]
                ):
                    return True
        except Exception:
            pass
        return False


def main():
    """CLI entry point."""
    import argparse

    ap = argparse.ArgumentParser(
        # --site-id and --accounts are required for an import but are checked
        # after parsing (--dump-text is an exclusive mode that needs neither),
        # so argparse cannot print them unbracketed on its own.
        usage=(
            "%(prog)s [-h] [--store-path STORE_PATH] [--dry-run] [-v] "
            "(--dump-text | --site-id SITE_ID --accounts ACCOUNTS) pdf_path"
        ),
        description="Import Metricool PDF analytics into SEO event store",
    )
    ap.add_argument("pdf_path", help="Path to Metricool PDF report (or .extracted.txt)")
    ap.add_argument("--site-id", default=None, help="Base site ID (required)")
    ap.add_argument(
        "--accounts",
        default=None,
        help="YAML/JSON file mapping each account display name in the report to a "
        f"role ({', '.join(ACCOUNT_ROLES)}); required",
    )
    ap.add_argument(
        "--store-path",
        default=None,
        help="Event store directory (default: seo_events under the seo-manager "
        "data home)",
    )
    ap.add_argument("--dry-run", action="store_true", help="Parse without storing")
    ap.add_argument(
        "--dump-text", action="store_true", help="Dump extracted text and exit"
    )
    ap.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    if args.dump_text:
        extractor = PDFTextExtractor()
        text = extractor.extract(args.pdf_path)
        print(text)
        return

    if not args.site_id:
        ap.error("--site-id is required")
    if not args.accounts:
        ap.error("--accounts is required (account display name -> role map)")

    store = None
    if not args.dry_run:
        store_path = (
            Path(args.store_path)
            if args.store_path
            else paths.data_home() / "seo_events"
        )
        store = SEOEventStore(store_path)

    importer = MetricoolPDFImporter(
        event_store=store, accounts=load_accounts(args.accounts)
    )
    counts = importer.import_pdf(
        args.pdf_path, site_id=args.site_id, dry_run=args.dry_run
    )

    print(
        f"Results: {counts['posts']} posts, {counts['community']} community, "
        f"{counts['demographics']} demographics, {counts['skipped']} skipped"
    )


if __name__ == "__main__":
    main()
