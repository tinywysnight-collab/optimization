# hascore

`assessment.resilience` is a read-only AWS resilience compliance scorer. It evaluates
Multi-AZ resilience in an account's primary Region and, for GS-001 patterns,
deployment or replication into the designated standby at `regions[1]`.

```python
from assessment.resilience import score

report = score({
    "accounts": [{
        "account_id": "123456789012",
        "pattern_id": "GS-001",
        "regions": ["us-east-1", "eu-west-1"],
        "application": {"name": "payments"},
    }],
})
```

The library returns a JSON-compatible dictionary by default. Pass
`output_format="html"` for a self-contained HTML report.

The scanner never creates, modifies, deletes, starts, stops, or tags AWS
resources. It uses read-only `Describe*`, `List*`, and `Get*` operations plus
`sts:AssumeRole` to access target accounts.
