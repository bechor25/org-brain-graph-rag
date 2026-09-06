# Public Interfaces

The new configuration is read by the group coordinator on startup.

* The broker accepts the following settings:

    * `group.coordinator.rebalance.protocols` — which protocols the coordinator offers.

    ```properties
    group.coordinator.rebalance.protocols=classic,consumer
    group.consumer.session.timeout.ms=45000

    group.consumer.heartbeat.interval.ms=5000
    ```

    * The consumer side is configured symmetrically:

      ```java
      props.put(ConsumerConfig.GROUP_PROTOCOL_CONFIG, "consumer");
      props.put(ConsumerConfig.GROUP_REMOTE_ASSIGNOR_CONFIG, "uniform");
      ```

* The coordinator exposes the state through a nested table:

        | Field | Type | Meaning |
        | --- | --- | --- |
        | MemberEpoch | int32 | the epoch the member last reconciled |
        | TargetEpoch | int32 | the epoch the coordinator wants |

## Compatibility

Existing consumers keep the classic protocol until they are reconfigured.
