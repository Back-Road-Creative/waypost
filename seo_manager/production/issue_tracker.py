"""
Production Issue Tracker.

Simple JSON-backed issue tracker for production pipeline improvements.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

VALID_CATEGORIES = [
    "script_quality",
    "audio",
    "music_sfx",
    "video",
    "seo_metadata",
    "thumbnails",
    "pipeline",
]

VALID_PRIORITIES = ["P0", "P1", "P2"]


class ProductionIssueTracker:
    """
    Track production pipeline issues and improvements.

    Storage format:
    {
        "issues": {
            "issue_123": {
                "issue_id": "issue_123",
                "brand_id": "examplepodcast",
                "category": "audio",
                "title": "48kHz audio required",
                "description": "...",
                "priority": "P0",
                "phase": "Phase 2A",
                "status": "open",
                "created_at": "2026-02-07T22:00:00Z",
                "resolved_at": null,
                "resolution_notes": null
            }
        }
    }
    """

    def __init__(self, storage_path: Path):
        """
        Initialize issue tracker.

        Args:
            storage_path: Directory for storing issues
        """
        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.issues_file = self.storage_path / "production_issues.json"

        # Load or initialize
        self._load_issues()

    def add_issue(
        self,
        brand_id: str,
        category: str,
        title: str,
        description: str,
        priority: str,
        phase: Optional[str] = None,
    ) -> str:
        """
        Add a new production issue.

        Args:
            brand_id: Brand identifier
            category: Issue category
            title: Short title
            description: Detailed description
            priority: Priority level (P0, P1, P2)
            phase: Optional phase identifier

        Returns:
            issue_id for tracking
        """
        if category not in VALID_CATEGORIES:
            raise ValueError(
                f"Invalid category: {category}. Must be one of {VALID_CATEGORIES}"
            )

        if priority not in VALID_PRIORITIES:
            raise ValueError(
                f"Invalid priority: {priority}. Must be one of {VALID_PRIORITIES}"
            )

        issue_id = f"issue_{uuid.uuid4().hex[:8]}"

        issue = {
            "issue_id": issue_id,
            "brand_id": brand_id,
            "category": category,
            "title": title,
            "description": description,
            "priority": priority,
            "phase": phase,
            "status": "open",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "resolved_at": None,
            "resolution_notes": None,
        }

        self.data["issues"][issue_id] = issue
        self._save_issues()

        return issue_id

    def resolve_issue(self, issue_id: str, resolution_notes: str):
        """
        Mark issue as resolved.

        Args:
            issue_id: Issue identifier
            resolution_notes: Notes about the resolution
        """
        if issue_id not in self.data["issues"]:
            raise ValueError(f"Issue not found: {issue_id}")

        self.data["issues"][issue_id]["status"] = "resolved"
        self.data["issues"][issue_id]["resolved_at"] = (
            datetime.utcnow().isoformat() + "Z"
        )
        self.data["issues"][issue_id]["resolution_notes"] = resolution_notes

        self._save_issues()

    def get_open_issues(
        self, brand_id: Optional[str] = None, category: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get open issues, optionally filtered.

        Args:
            brand_id: Filter by brand (optional)
            category: Filter by category (optional)

        Returns:
            List of matching open issues
        """
        issues = []

        for issue in self.data["issues"].values():
            # Filter by status
            if issue["status"] != "open":
                continue

            # Filter by brand
            if brand_id and issue["brand_id"] != brand_id:
                continue

            # Filter by category
            if category and issue["category"] != category:
                continue

            issues.append(issue)

        return issues

    def get_report(self, brand_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Generate issue summary report.

        Args:
            brand_id: Filter by brand (optional)

        Returns:
            Report with counts and breakdowns
        """
        # Filter issues
        all_issues = [
            issue
            for issue in self.data["issues"].values()
            if not brand_id or issue["brand_id"] == brand_id
        ]

        open_issues = [issue for issue in all_issues if issue["status"] == "open"]
        resolved_issues = [
            issue for issue in all_issues if issue["status"] == "resolved"
        ]

        # Count by category
        by_category = {}
        for issue in open_issues:
            cat = issue["category"]
            by_category[cat] = by_category.get(cat, 0) + 1

        # Count by priority
        by_priority = {}
        for issue in open_issues:
            pri = issue["priority"]
            by_priority[pri] = by_priority.get(pri, 0) + 1

        return {
            "total": len(all_issues),
            "open": len(open_issues),
            "resolved": len(resolved_issues),
            "by_category": by_category,
            "by_priority": by_priority,
        }

    def _load_issues(self):
        """Load issues from disk"""
        if self.issues_file.exists():
            with open(self.issues_file) as f:
                self.data = json.load(f)
        else:
            self.data = {"issues": {}}
            self._save_issues()

    def _save_issues(self):
        """Save issues to disk"""
        with open(self.issues_file, "w") as f:
            json.dump(self.data, f, indent=2)
