# Plans

One file per build step, written and reviewed before the code existed (see `CLAUDE.md`). They are a historical
record: each ends with the amendments made after plan review, and later sections override earlier text where they
differ (for example step 3 first planned top-20 unconfigured agent labels and later removed them).

1. `step-1.md`: audit reader, outcome classification, SLIs, config
2. `step-2.md`: slo-toolkit dependency, error budget, multi-window burn rates
3. `step-3.md`: Prometheus textfile metrics and alert rules
4. `step-4.md`: weekly Markdown report
5. `step-5.md`: synthetic data generator and CLI
6. `step-6.md`: README, error budget policy, example config

For current behaviour read the README.
