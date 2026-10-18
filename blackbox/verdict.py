from .checks import Status

HEALTHY, WARNING, CRITICAL = "Healthy", "Warning", "Critical"


def decide(results):
    """Any Critical -> Critical, any Warning -> Warning, else Healthy. Not available is ignored."""
    statuses = {r.status for r in results}
    if Status.CRITICAL in statuses:
        return CRITICAL
    if Status.WARNING in statuses:
        return WARNING
    return HEALTHY


def findings(results):
    """Warning and Critical results, Critical first, for the Findings sheet and debrief table."""
    bad = [r for r in results if r.status in (Status.CRITICAL, Status.WARNING)]
    return sorted(bad, key=lambda r: (r.status != Status.CRITICAL, r.id))
