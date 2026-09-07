# Entity gold — 10 pairs to check by hand

The other 90 rows of `data/eval/resolution_gold.jsonl` are the adjudicator's own
verdicts, so they cannot grade the adjudicator. These ten are the audit that can:
for each pair, decide yourself whether the two are one thing, and compare.

`same` means the agent merged them. `different` means it left them apart.

## 1. verdict: **different**  (cosine 0.8564)

- **A** `Decision|a leader being removed stay leader until the record commit or the epoch advance` — a leader being removed stays leader until the record commits or the epoch advances
- **B** `Decision|a removed leader keep serving fetch but is not part of the majority` — a removed leader keeps serving Fetch but is not part of the majority
- agent's reason: One KIP-853 quote states how long a removed leader keeps leadership, the other that it serves Fetch while excluding itself from the majority.
- your verdict: `same` / `different` / `unsure` -> **different** (planner)

## 2. verdict: **different**  (cosine 0.8529)

- **A** `Decision|add a migration znode tracking the offset written back to zk` — add a /migration ZNode tracking the offset written back to ZK
- **B** `Decision|write metadata to both the kraft log and zookeeper during the migration` — write metadata to both the KRaft log and ZooKeeper during the migration
- agent's reason: One KIP-866 quote introduces the ZNode tracking the offset written back to ZK, the other the dual-write decision itself.
- your verdict: `same` / `different` / `unsure` -> **different** (planner)

## 3. verdict: **different**  (cosine 0.9098)

- **A** `Decision|cap the per partition restore buffer with a new config` — cap the per-partition restore buffer with a new config
- **B** `Decision|default the restore buffer cap to 10000 record` — default the restore buffer cap to 10000 records
- agent's reason: One KIP-1325 quote introduces the config `restore.buffered.records.per.partition` itself, the other states the compatibility impact of its default (unbounded becomes 10000 records per partition).
- your verdict: `same` / `different` / `unsure` -> **different** (planner)

## 4. verdict: **different**  (cosine 0.8644)

- **A** `Decision|grant the admin principal create on an offset topic that doe not exist yet` — grant the admin principal Create on an offsets topic that does not exist yet
- **B** `Decision|grant the connector consumer principal read on the offset topic` — grant the connector consumer principal Read on the offsets topic
- agent's reason: KIP-618 requires Create for the connector's admin principal on a missing offsets topic and Read for its consumer principal on that topic: two different principals and permissions.
- your verdict: `same` / `different` / `unsure` -> **different** (planner)

## 5. verdict: **different**  (cosine 0.9157)

- **A** `Decision|the group coordinator serve altersharegroupoffset` — the group coordinator serves AlterShareGroupOffsets
- **B** `Decision|the group coordinator serve deletesharegroupoffset` — the group coordinator serves DeleteShareGroupOffsets
- agent's reason: KIP-932 defines AlterShareGroupOffsets to alter share-partition start offsets and DeleteShareGroupOffsets to delete them, two separate APIs.
- your verdict: `same` / `different` / `unsure` -> **different** (planner)

## 6. verdict: **same**  (cosine 0.8882)

- **A** `Decision|add a new value client_metric to configresource type` — add a new value CLIENT_METRICS to ConfigResource.Type
- **B** `Decision|configure metrics subscription through the client_metric config resource type` — configure metrics subscriptions through the CLIENT_METRICS config resource type
- agent's reason: Two chunks of KIP-714 state the same decision, administering metrics subscriptions through the Admin config API with the new CLIENT_METRICS ConfigResource type.
- your verdict: `same` / `different` / `unsure` -> **same** (planner)

## 7. verdict: **same**  (cosine 0.8572)

- **A** `Decision|add deliverycompletecount as a tagged field without bumping the record version` — add DeliveryCompleteCount as a tagged field without bumping the record version
- **B** `Decision|the tagged field keep old and new broker compatible` — the tagged field keeps old and new brokers compatible
- agent's reason: Two chunks of KIP-1226 state the one decision that the new field is added as a tagged field with a default instead of bumping the record version, so old and new brokers stay compatible.
- your verdict: `same` / `different` / `unsure` -> **same (החלטה + ההשלכה שלה — מקבל)** (planner)

## 8. verdict: **same**  (cosine 0.9325)

- **A** `Decision|once tier storage is enabled for a topic it can not be disabled` — once tier storage is enabled for a topic, it can not be disabled
- **B** `Decision|remote storage enable cannot be disabled once it is enabled` — remote.storage.enable cannot be disabled once it is enabled
- agent's reason: Two chunks of KIP-405 state the one rule that remote/tiered storage cannot be turned off once enabled for a topic, both adding that the capability comes in a future version.
- your verdict: `same` / `different` / `unsure` -> **same** (planner)

## 9. verdict: **same**  (cosine 0.95)

- **A** `Decision|thi ticket cannot be worked on until the apache kafka 5 0 release` — this ticket cannot be worked on until the Apache Kafka 5.0 release
- **B** `Decision|thi ticket cannot be worked on until we do apache kafka 5 0 release` — THIS TICKET CANNOT BE WORKED ON, UNTIL WE DO APACHE KAFKA 5.0 RELEASE.
- agent's reason: The quotes are the byte-identical pasted banner 'THIS TICKET CANNOT BE WORKED ON, UNTIL WE DO APACHE KAFKA 5.0 RELEASE.', which side a already carries across two tickets, so this is one deferral statement under a normalized and a verbatim name.
- your verdict: `same` / `different` / `unsure` -> **different (banner מודבק בין tickets — ה-guard צדק לדלג)** (planner)

## 10. verdict: **same**  (cosine 0.8968)

- **A** `Feature|assignment epoch per member and partition` — assignment epoch per member and partition
- **B** `Feature|per member and per partition assignment epoch in the group model` — per-member and per-partition assignment epoch in the group model
- agent's reason: Two chunks of KAFKA-19779 describe the same addition, one integer assignment epoch per assigned partition per member.
- your verdict: `same` / `different` / `unsure` -> **same** (planner)


## Planner tally (2026-09-07)

9/10 agree with the adjudicator; the single disagreement (#9) is the pasted banner across tickets, which the boilerplate guard already refused to merge. Pending the user's own pass on the same 10 rows.
