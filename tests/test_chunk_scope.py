"""Phase A scope: what gets chunked, what is deliberately left out, and `--limit`."""

from __future__ import annotations

from brain.canon.models import Comment, Ref
from brain.chunk.chunker import ChunkStats
from brain.chunk.scope import iter_chunks, referenced_kip_keys, select
from tests.graph_helpers import change, corpus, document, work_item


def kip(key: str, **kw):
    return document(key=key, body_md=f"# {key}\n\n" + "rebalance protocol " * 60, **kw)


def slice_corpus():
    return corpus(
        documents=[
            kip("KIP-848"),
            kip("KIP-500"),
            kip("KIP-999"),  # nothing in the slice names it
            document(
                key="confluence:61309558",
                body_md="# KIP-848 the other one\n\n" + "rebalance " * 80,
                kip_of="KIP-848",
                labels=["kip-variant", "ambiguous-kip"],
            ),
            document(
                key="confluence:70000000",
                body_md="# KIP-500 a plain variant\n\n" + "quorum " * 80,
                kip_of="KIP-500",
                labels=["kip-variant"],
            ),
        ],
        workitems=[
            work_item(key="KAFKA-1", description="a" * 300, refs=[Ref(kind="kip", key="KIP-848")]),
            work_item(key="KAFKA-2", description="b" * 300),
        ],
        changes=[
            change(
                id="a" * 40,
                message="KAFKA-1: fix the rebalance timeout",
                refs=[Ref(kind="issue", key="KAFKA-1")],
            ),
            change(
                id="b" * 40,
                message="KIP-500: groundwork for the quorum controller",
                refs=[Ref(kind="kip", key="KIP-500")],
            ),
            change(id="c" * 40, message="MINOR: fix a typo in the javadoc", refs=[]),
            change(
                id="pr:1",
                kind="pr",
                message="KIP-999 in a PR",
                refs=[Ref(kind="kip", key="KIP-999")],
            ),
        ],
    )


def test_referenced_kips_come_from_work_items_and_commits_only():
    # The PR names KIP-999; the brief scopes Phase A to WorkItem and Commit refs.
    assert referenced_kip_keys(slice_corpus()) == {"KIP-848", "KIP-500"}


def test_scope_keeps_referenced_kips_and_their_ambiguous_variants():
    scope = select(slice_corpus())
    assert [d.key for d in scope.documents] == ["KIP-500", "KIP-848", "confluence:61309558"]
    assert scope.stats["documents_reachable"] == 2
    assert scope.stats["documents_ambiguous_variants"] == 1
    # The plain `kip-variant` of KIP-500 is not an `ambiguous-kip`, so it stays out.
    assert scope.stats["documents_skipped_unreferenced"] == 2


def test_all_docs_takes_everything_and_says_so():
    scope = select(slice_corpus(), all_docs=True)
    assert len(scope.documents) == 5
    assert scope.stats["all_docs"] is True
    assert scope.stats["documents_skipped_unreferenced"] == 0


def test_only_commits_that_name_an_issue_or_a_kip_are_chunked():
    scope = select(slice_corpus())
    assert [c.id for c in scope.commits] == ["a" * 40, "b" * 40]
    assert scope.stats["commits_skipped_unkeyed"] == 1
    assert scope.stats["commits_total"] == 3  # the PR is not a commit


def test_every_work_item_is_in_scope_whatever_it_references():
    scope = select(slice_corpus())
    assert [w.key for w in scope.workitems] == ["KAFKA-1", "KAFKA-2"]


def test_a_kip_reference_outside_the_corpus_is_counted_not_invented():
    c = slice_corpus()
    c.workitems[0].refs.append(Ref(kind="kip", key="KIP-4242"))
    scope = select(c)
    assert scope.stats["kip_keys_referenced"] == 3
    assert scope.stats["kip_keys_outside_corpus"] == 1
    assert all(d.key != "KIP-4242" for d in scope.documents)


def test_limit_takes_a_proportional_sample_of_each_kind():
    scope = select(slice_corpus(), limit=4)
    assert scope.parents() <= 4
    assert scope.documents and scope.workitems and scope.commits
    assert scope.stats["limit"]["applied"] is True


def test_limit_larger_than_the_corpus_changes_nothing():
    scope = select(slice_corpus(), limit=1000)
    assert scope.stats["limit"] == {"requested": 1000, "applied": False, "parents": 7}


def test_kinds_select_which_texts_are_chunked():
    c = slice_corpus()
    c.workitems[0].comments = [Comment(author="dlee", body="a comment long enough to keep")]
    scope = select(c)
    everything = list(iter_chunks(scope, {"doc", "issue", "comment", "commit"}, ChunkStats()))
    assert {x.kind for x in everything} == {"section", "description", "comment", "message"}
    only_docs = list(iter_chunks(scope, {"doc"}, ChunkStats()))
    assert {x.parent_kind for x in only_docs} == {"Document"}
    no_comments = list(iter_chunks(scope, {"issue"}, ChunkStats()))
    assert {x.kind for x in no_comments} == {"description"}
