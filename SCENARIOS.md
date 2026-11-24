# Rollout scenarios

These fixtures make the recovery decision observable without a cluster.

| Scenario | Controller signal | Checker result | Operator action |
|---|---|---|---|
| Healthy | `Available=True`, all desired replicas available | Exit 0 / `complete` | Continue traffic or close the change |
| Stalled rollout | `Progressing=False` with `ProgressDeadlineExceeded` | Exit 2 / `stalled` | Inspect events and revision history |
| Replica creation failure | `ReplicaFailure=True` | Exit 2 / `stalled` | Capture the reason/message before rollback |
| Converging rollout | Observed generation or replica counts lag | Exit 1 / `waiting` | Recheck with a fresh snapshot |
| Wrong recovery image | Healthy counters but unexpected template image | Exit 4 / `image_mismatch` | Stop promotion and correct the image reference |

The checker deliberately separates **waiting** from **stalled** so automation can retry transient convergence but page an operator for a controller failure. The JSON details preserve replica counts and condition reasons for the incident timeline.
