"""agent-slo: SLIs, error budgets and burn-rate alerts for agent-harness audit logs.

Pipeline: ``audit.read_audit`` -> ``sli.compute_slis`` / ``budget.evaluate_budget`` ->
``metrics.render_metrics``, ``rules.render_rules``, ``report.render_report``. See the README.
"""
