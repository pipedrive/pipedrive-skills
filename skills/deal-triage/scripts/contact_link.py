"""Build the one permitted contact-link edit from fresh Pipedrive responses."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import write_rules  # noqa: E402


def _record(response, label):
    if not isinstance(response, dict) or response.get("success") is not True:
        raise ValueError(f"{label} response is not a successful Pipedrive read")
    return response.get("data")


def contact_link_params(deal_response, activity_response, deal_id, activity_id, person_id):
    deal_response = write_rules.unwrap_capture(deal_response)
    activity_response = write_rules.unwrap_capture(activity_response)
    deal = _record(deal_response, "Deal")
    if (not isinstance(deal, dict) or deal.get("id") != deal_id
            or deal.get("status") != "open" or deal.get("is_deleted") is True
            or deal.get("is_archived") is True):
        raise ValueError("Deal is missing, changed, or no longer open")
    if not isinstance(person_id, int) or isinstance(person_id, bool) or person_id <= 0:
        raise ValueError("Accepted contact ID is invalid")
    if deal.get("person_id") != person_id:
        raise ValueError("The deal's linked contact differs from the accepted contact")

    activities = _record(activity_response, "Activity")
    if not isinstance(activities, list):
        raise ValueError("Activity read did not return a list")
    matches = [activity for activity in activities if isinstance(activity, dict)
               and activity.get("id") == activity_id]
    if len(matches) != 1:
        raise ValueError("The requested activity was not returned exactly once")
    activity = matches[0]
    if (activity.get("deal_id") != deal_id or activity.get("done") is not False
            or activity.get("is_deleted") is True or activity.get("lead_id") is not None):
        raise ValueError("Activity is no longer an open activity on the accepted deal")
    if "person_id" not in activity or activity["person_id"] is not None:
        raise ValueError("Activity already has a linked contact or its contact is unverified")
    if activity.get("participants") != []:
        raise ValueError("Activity participants are present or unverified")

    return {"id": activity_id, "participants": [{"person_id": person_id, "primary": True}]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deal-response", required=True, type=Path)
    parser.add_argument("--activity-response", required=True, type=Path)
    parser.add_argument("--deal-id", required=True, type=int)
    parser.add_argument("--activity-id", required=True, type=int)
    parser.add_argument("--person-id", required=True, type=int)
    args = parser.parse_args()
    try:
        params = contact_link_params(
            json.loads(args.deal_response.read_text()),
            json.loads(args.activity_response.read_text()),
            args.deal_id, args.activity_id, args.person_id,
        )
    except (OSError, json.JSONDecodeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps({"tool": "updateActivity", "params": params}, separators=(",", ":")))


if __name__ == "__main__":
    main()
