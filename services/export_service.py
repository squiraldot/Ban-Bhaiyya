import csv
import io
import json


def to_csv(report):
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["metric", "value"])
    for key in ("days", "joins", "warnings", "actions"):
        writer.writerow([key, report[key]])
    writer.writerow([])
    writer.writerow(["warning_reason", "count"])
    for key, value in report["warning_reasons"].items():
        writer.writerow([key, value])
    writer.writerow([])
    writer.writerow(["action", "count"])
    for key, value in report["actions_by_type"].items():
        writer.writerow([key, value])
    return out.getvalue().encode("utf-8")


def to_json(report):
    payload = dict(report)
    payload["warning_reasons"] = dict(report["warning_reasons"])
    payload["actions_by_type"] = dict(report["actions_by_type"])
    return json.dumps(payload, indent=2, default=str).encode("utf-8")
