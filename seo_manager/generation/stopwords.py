"""
Vendored English stopword list for keyword extraction.

Verbatim copy of NLTK's ``corpora/stopwords/english`` (198 entries), which
NLTK took from the PostgreSQL Snowball stopword lists (BSD-licensed) and
augmented (nltk/nltk_data#22). A list of common function words carries no
creative content and is redistributable; provenance is recorded here.

Why vendored instead of downloaded
----------------------------------
``_extract_keywords`` used to ``nltk.download("stopwords")`` at run time and,
when that failed, *silently* fell back to a ~10-word hardcoded set plus a
length filter. Keyword extraction then returned different results with no
error, no warning and no signal — the two code paths simply disagreed.

That is not hypothetical. Some nltk releases ship an import hook that refuses
any nltk-initiated import resolving inside the current working directory, so
a virtualenv built inside the project directory makes ``import nltk`` itself
raise ImportError, the silent fallback engages, and extraction quietly
changes behaviour. A missing corpus or an offline runner produce the same
class of degradation.

A word list living in source cannot go missing, cannot need the network, and
cannot disagree with a second copy of itself. There is deliberately no
fallback path and no second list.
"""

_ENGLISH_STOPWORDS_TEXT = """
    a about above after again against ain all am an and any are aren
    aren't as at be because been before being below between both but by
    can couldn couldn't d did didn didn't do does doesn doesn't doing
    don don't down during each few for from further had hadn hadn't has
    hasn hasn't have haven haven't having he he'd he'll her here hers
    herself he's him himself his how i i'd if i'll i'm in into is isn
    isn't it it'd it'll it's its itself i've just ll m ma me mightn
    mightn't more most mustn mustn't my myself needn needn't no nor not
    now o of off on once only or other our ours ourselves out over own
    re s same shan shan't she she'd she'll she's should shouldn
    shouldn't should've so some such t than that that'll the their
    theirs them themselves then there these they they'd they'll they're
    they've this those through to too under until up ve very was wasn
    wasn't we we'd we'll we're were weren weren't we've what when where
    which while who whom why will with won won't wouldn wouldn't y you
    you'd you'll your you're yours yourself yourselves you've
"""

ENGLISH_STOPWORDS = frozenset(_ENGLISH_STOPWORDS_TEXT.split())
