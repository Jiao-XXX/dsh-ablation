The file config/services.toml declares service reliability settings. Every entry must satisfy this invariant:

    timeout_ms < retries * backoff_ms

Audit every entry in the file and find all entries that VIOLATE the invariant.

Write your findings to answer.txt in the working directory, one violating service id per line, in ascending order, with no other text. For example, if svc_004 and svc_012 were the only violations, answer.txt would contain exactly:

svc_004
svc_012

Answer in English.
