from brain.canon.mentions import extract_refs, filter_refs


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


def test_allowlist_removes_pseudo_keys_and_keeps_the_reason():
    refs = extract_refs("KAFKA-15123 encoded as UTF-8 with SHA-256 and AES-128")
    kept, removed = filter_refs(refs)
    assert [(r.kind, r.key) for r in kept] == [("issue", "KAFKA-15123")]
    assert removed == [
        ("UTF-8", "not_in_allowlist"),
        ("SHA-256", "not_in_allowlist"),
        ("AES-128", "not_in_allowlist"),
    ]


def test_synthetic_prefixes_are_allowlisted_by_default():
    """`brain canon` carries the Xray/ADO layer over; its refs must survive the filter."""
    text = "XE-3 executes XT-12 and XT-13 from XP-2 / XS-4; blocked by ADO-77"
    kept, removed = filter_refs(extract_refs(text))
    assert [r.key for r in kept] == ["XE-3", "XT-12", "XT-13", "XP-2", "XS-4", "ADO-77"]
    assert removed == []


def test_blacklist_removes_the_kip_template_placeholder():
    kept, removed = filter_refs(extract_refs("JIRA: KAFKA-1 (replace me), see KAFKA-2"))
    assert [r.key for r in kept] == ["KAFKA-2"]
    assert removed == [("KAFKA-1", "blacklisted")]


def test_filter_leaves_non_issue_refs_alone():
    text = "KIP-848 and (#14567) and @jrao and https://example.com/x and UTF-8"
    kept, removed = filter_refs(extract_refs(text))
    assert [r.kind for r in kept] == ["kip", "pr", "user", "url"]
    assert [k for k, _ in removed] == ["UTF-8"]


def test_allowlist_is_extensible_per_call():
    kept, _ = filter_refs(extract_refs("JETTY-77"), allowlist={"KAFKA", "JETTY"})
    assert [r.key for r in kept] == ["JETTY-77"]


def test_jira_mention_syntax_is_a_user_ref():
    assert keys("[~chia7712] thanks, cc @jrao") == [("user", "chia7712"), ("user", "jrao")]


def test_the_two_mention_syntaxes_collapse_to_one_ref():
    assert keys("[~jrao] see @jrao") == [("user", "jrao")]


def test_a_lowercase_key_of_an_allowlisted_project_is_normalized():
    assert keys("fixes kafka-15123 and Kafka-9") == [
        ("issue", "KAFKA-15123"),
        ("issue", "KAFKA-9"),
    ]


def test_a_lowercase_non_project_token_keeps_its_case_and_is_filtered_out():
    kept, removed = filter_refs(extract_refs("encoded utf-8, since pre-2023, see kafka-1234"))

    assert [r.key for r in kept] == ["KAFKA-1234"]
    assert removed == [("utf-8", "not_in_allowlist"), ("pre-2023", "not_in_allowlist")]


def test_normalization_follows_a_widened_allowlist():
    assert [r.key for r in extract_refs("jetty-77", allowlist={"KAFKA", "JETTY"})] == ["JETTY-77"]
    assert [r.key for r in extract_refs("jetty-77")] == ["jetty-77"]


def test_a_lowercase_key_in_a_browse_url_is_normalized_too():
    assert keys("https://issues.apache.org/jira/browse/kafka-15123") == [("issue", "KAFKA-15123")]


def test_kip_is_still_not_an_issue_in_any_case():
    assert keys("kip-848") == [("kip", "KIP-848")]


def test_a_lowercase_key_in_a_hostname_or_port_is_not_an_issue():
    """`kafka-2` in a broker address is a pod, not KAFKA-2."""
    text = (
        "Error connecting to node kafka-2.kafka.myproject.svc.cluster.local "
        "with quorum.voters=1@kafka-1:9092 — unlike kafka-15123, which is an issue."
    )
    assert keys(text) == [("issue", "KAFKA-15123")]


def test_an_uppercase_key_is_never_judged_by_what_follows_it():
    assert keys("KAFKA-15123:fix the thing") == [("issue", "KAFKA-15123")]
