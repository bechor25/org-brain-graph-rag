from brain.canon.mentions import extract_refs


def keys(text):
    return [(r.kind, r.key) for r in extract_refs(text)]


def test_issue_keys():
    assert keys("Fixes KAFKA-15123 and relates to KAFKA-9") == [
        ("issue", "KAFKA-15123"),
        ("issue", "KAFKA-9"),
    ]


def test_kip_is_not_an_issue_and_is_normalized():
    assert keys("see kip-848 and KIP-932") == [("kip", "KIP-848"), ("kip", "KIP-932")]


def test_pr_numbers():
    assert keys("Merged (#14567). Not a ref: KAFKA#1 or a#12") == [("pr", "14567")]


def test_urls_are_classified():
    text = (
        "https://issues.apache.org/jira/browse/KAFKA-15123 "
        "https://cwiki.apache.org/confluence/display/KAFKA/KIP-848%3A+Next+Gen "
        "https://github.com/apache/kafka/pull/14567 "
        "https://example.com/other"
    )
    assert keys(text) == [
        ("issue", "KAFKA-15123"),
        ("kip", "KIP-848"),
        ("pr", "14567"),
        ("url", "https://example.com/other"),
    ]


def test_users_and_dedupe():
    assert keys("@jrao @jrao thanks @rao.jun") == [("user", "jrao"), ("user", "rao.jun")]


def test_hebrew_text_still_finds_keys():
    assert keys("ראו KAFKA-100 ו-KIP-5 לפרטים") == [("issue", "KAFKA-100"), ("kip", "KIP-5")]
