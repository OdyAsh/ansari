"""Regenerate `metaScriptOracle.ts` from the legacy operator script (issue #358, plan phase 7).

Why this exists: the Python script `legacy/whatsapp/scripts/check_meta_subscriptions.py` has no tests, and what it PRINTS is the
whole product (an operator reads it to decide whether to change Meta's state). So that "the TypeScript script says the same thing"
is checked rather than assumed, this runs the legacy functions with a fake `requests` and scripted answers to `input()`, records
every screen and every call made, and writes them as data the Vitest tests compare with.

Run from the repo root, with a Python that has `requests` and `python-dotenv` (the legacy service's environment):

    cd legacy/whatsapp
    uv run python ../../apps/whatsapp/tests/fixtures/generate_meta_script_oracle.py ../../apps/whatsapp/tests/fixtures/metaScriptOracle.ts

It never talks to the network. Delete this file together with `legacy/whatsapp`; `metaScriptOracle.ts` stays valid because it is
plain data.

The recorded screens are Python's own output. Where the TypeScript script deliberately differs, the tests say so and name the
difference; the table `EXPECTED_DIFFERENCES` at the end lists them so the data and the tests cannot drift apart silently.
"""

import builtins
import contextlib
import importlib.util
import io
import json
import os
import sys
import time

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "legacy", "whatsapp", "scripts", "check_meta_subscriptions.py")

os.environ["TZ"] = "UTC"
if hasattr(time, "tzset"):
    time.tzset()

spec = importlib.util.spec_from_file_location("legacy_script", os.path.abspath(SCRIPT))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

TOKEN = "SECRET-TOKEN-VALUE-0123456789"
mod.ACCESS_TOKEN = TOKEN
mod.WABA_ID = "111"


class Resp:
    def __init__(self, status, body, raw=None):
        self.status_code = status
        self._body = body
        self.text = raw if raw is not None else json.dumps(body)

    def json(self):
        if self._body is _NOT_JSON:
            raise ValueError("not json")
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            error = requests.exceptions.HTTPError(f"{self.status_code} Client Error: for url: x")
            error.response = self
            raise error


_NOT_JSON = object()


def run(fn, answers, inputs=()):
    """Run one legacy function. Returns the lines it printed, the prompts it asked and the calls it made."""
    calls = []
    prompts = []
    remaining = list(answers)

    class FakeRequests:
        import requests as _real

        exceptions = _real.exceptions

        @staticmethod
        def _do(method, url, headers=None, params=None):
            query = "&".join(f"{k}={v}" for k, v in (params or {}).items())
            path = url.replace("https://graph.facebook.com/v26.0", "")
            calls.append({"method": method, "path": path, "query": query, "bearer": (headers or {}).get("Authorization") == f"Bearer {TOKEN}"})
            answer = remaining.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

    FakeRequests.get = staticmethod(lambda url, headers=None, params=None: FakeRequests._do("GET", url, headers, params))
    FakeRequests.post = staticmethod(lambda url, headers=None, params=None: FakeRequests._do("POST", url, headers, params))
    FakeRequests.delete = staticmethod(lambda url, headers=None, params=None: FakeRequests._do("DELETE", url, headers, params))
    mod.requests = FakeRequests

    typed = iter(inputs)

    def fake_input(prompt=""):
        prompts.append(prompt)
        return next(typed)

    real_input = builtins.input
    builtins.input = fake_input
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            fn()
    finally:
        builtins.input = real_input
    text = out.getvalue()
    lines = text[:-1].split("\n") if text.endswith("\n") else text.split("\n")
    return {"lines": lines, "prompts": prompts, "calls": calls}


def tok(app_id, name):
    return Resp(200, {"data": {"app_id": app_id, "application": name}})


APPS = {
    "data": [
        {"whatsapp_business_api_data": {"id": "1", "name": "Ansari - Staging", "link": "https://www.facebook.com/games/?app_id=1"}},
        {"whatsapp_business_api_data": {"id": "2", "name": "WA DevX Webhook Events 1P App"}},
    ]
}
ONE_APP = {"data": [{"whatsapp_business_api_data": {"id": "1", "name": "Ansari - Staging"}}]}
SAME_NAME_OTHER_ID = {"data": [{"whatsapp_business_api_data": {"id": "2", "name": "Ansari - Staging"}}]}
NO_INNER = {"data": [{"something_else": {"id": "3"}}, {"whatsapp_business_api_data": {"id": "1"}}]}

TOKEN_DETAILS = {
    "data": {
        "app_id": "1",
        "application": "Ansari - Staging",
        "type": "SYSTEM_USER",
        "is_valid": True,
        "issued_at": 1700000000,
        "expires_at": 0,
        "data_access_expires_at": 1800000000,
        "user_id": "77",
        "scopes": ["whatsapp_business_messaging", "business_management"],
        "granular_scopes": [{"scope": "whatsapp_business_management", "target_ids": ["111"]}, "plain"],
        "metadata": {"sso": "none", "n": 1},
        "extra_field": {"a": [1, None, True]},
    }
}
TOKEN_EXPIRING = {"data": {"app_id": "1", "application": "A", "issued_at": 0, "expires_at": 1900000000, "is_valid": False, "error": {"code": 190}}}
TOKEN_MINIMAL = {"data": {}}

# (name, function, meta answers in order, typed inputs)
CASES = {
    "waba_details": (mod.get_waba_details, [Resp(200, {"id": "111", "name": "Test WABA", "timezone_id": "1", "message_template_namespace": "ns", "account_review_status": "APPROVED"})], ()),
    "waba_details_sparse": (mod.get_waba_details, [Resp(200, {"id": "111"})], ()),
    "waba_details_http_error": (mod.get_waba_details, [Resp(400, {"error": {"message": "Bad token"}})], ()),
    "token_full": (mod.debug_access_token, [Resp(200, TOKEN_DETAILS)], ()),
    "token_expiring": (mod.debug_access_token, [Resp(200, TOKEN_EXPIRING)], ()),
    "token_minimal": (mod.debug_access_token, [Resp(200, TOKEN_MINIMAL)], ()),
    "token_http_error": (mod.debug_access_token, [Resp(400, {"error": {"message": "Nope"}})], ()),
    "subscribed_two": (mod.check_subscribed_apps, [Resp(200, APPS)], ()),
    "subscribed_none": (mod.check_subscribed_apps, [Resp(200, {"data": []})], ()),
    "subscribed_entry_without_inner": (mod.check_subscribed_apps, [Resp(200, NO_INNER)], ()),
    "subscribed_no_data_key": (mod.check_subscribed_apps, [Resp(200, {})], ()),
    "subscribed_http_error": (mod.check_subscribed_apps, [Resp(403, {"error": {"message": "Forbidden"}})], ()),
    "phones_two": (mod.list_phone_numbers, [Resp(200, {"data": [{"id": "9", "display_phone_number": "+1 555", "verified_name": "V", "quality_rating": "GREEN", "code_verification_status": "VERIFIED"}, {"id": "10"}]})], ()),
    # Python prints `phone.get('id')` as it is: `None` when the id is missing or null
    "phones_missing_id": (mod.list_phone_numbers, [Resp(200, {"data": [{}, {"id": None, "verified_name": None}]})], ()),
    "phones_none": (mod.list_phone_numbers, [Resp(200, {})], ()),
    "phones_empty_list": (mod.list_phone_numbers, [Resp(200, {"data": []})], ()),
    "subscribe_yes": (mod.subscribe_waba, [Resp(200, {"success": True})], ["yes"]),
    "subscribe_YES_spaces": (mod.subscribe_waba, [Resp(200, {"success": True})], ["  YES "]),
    "subscribe_no": (mod.subscribe_waba, [], ["no"]),
    "subscribe_blank": (mod.subscribe_waba, [], [""]),
    "subscribe_y": (mod.subscribe_waba, [], ["y"]),
    "subscribe_success_false": (mod.subscribe_waba, [Resp(200, {"success": False})], ["yes"]),
    "subscribe_http_error": (mod.subscribe_waba, [Resp(400, {"error": {"message": "Denied"}})], ["yes"]),
    "unsub_ready_yes": (mod.unsubscribe_waba, [tok("1", "Ansari - Staging"), Resp(200, {"name": "Test WABA"}), Resp(200, APPS), Resp(200, {"success": True})], ["yes"]),
    "unsub_ready_no": (mod.unsubscribe_waba, [tok("1", "Ansari - Staging"), Resp(200, {"name": "Test WABA"}), Resp(200, APPS)], ["no"]),
    "unsub_ready_only_app": (mod.unsubscribe_waba, [tok("1", "Ansari - Staging"), Resp(200, {"name": "Test WABA"}), Resp(200, ONE_APP), Resp(200, {"success": True})], ["yes"]),
    "unsub_token_app_not_subscribed": (mod.unsubscribe_waba, [tok("9", "Mine"), Resp(200, {"name": "Test WABA"}), Resp(200, APPS)], ()),
    "unsub_nothing_subscribed": (mod.unsubscribe_waba, [tok("1", "Ansari - Staging"), Resp(200, {"name": "Test WABA"}), Resp(200, {"data": []})], ()),
    "unsub_no_data_key": (mod.unsubscribe_waba, [tok("1", "Ansari - Staging"), Resp(200, {"name": "Test WABA"}), Resp(200, {})], ()),
    "unsub_token_unreadable": (mod.unsubscribe_waba, [Resp(200, {})], ()),
    "unsub_token_http_error": (mod.unsubscribe_waba, [Resp(401, {"error": {"message": "x"}})], ()),
    "unsub_waba_name_fails": (mod.unsubscribe_waba, [tok("1", "Ansari - Staging"), Resp(400, {"error": {}}), Resp(200, APPS), Resp(200, {"success": True})], ["yes"]),
    "unsub_subscribed_fails": (mod.unsubscribe_waba, [tok("1", "Ansari - Staging"), Resp(200, {"name": "Test WABA"}), Resp(500, {"error": {}})], ()),
    "unsub_delete_fails": (mod.unsubscribe_waba, [tok("1", "Ansari - Staging"), Resp(200, {"name": "Test WABA"}), Resp(200, APPS), Resp(400, {"error": {"message": "cannot"}})], ["yes"]),
    # Python matches by NAME. These two cases are the ones where matching by ID differs.
    "unsub_same_name_other_id": (mod.unsubscribe_waba, [tok("1", "Ansari - Staging"), Resp(200, {"name": "Test WABA"}), Resp(200, SAME_NAME_OTHER_ID), Resp(200, {"success": True})], ["yes"]),
    "unsub_same_id_other_name": (mod.unsubscribe_waba, [tok("1", "Renamed"), Resp(200, {"name": "Test WABA"}), Resp(200, ONE_APP)], ()),
}


def context_case(waba_answer, token_answer):
    """What `get_current_context` returns for the two answers."""
    mod.requests = type(
        "R",
        (),
        {
            "get": staticmethod(lambda url, headers=None, params=None: waba_answer if "debug_token" not in url else token_answer),
        },
    )
    return list(mod.get_current_context())


def main(out_path):
    data = {name: run(fn, answers, inputs) for name, (fn, answers, inputs) in CASES.items()}

    # Static screens, verbatim
    for name, fn in (("prerequisite_knowledge", mod.show_prerequisite_knowledge), ("miscellaneous_info", mod.show_miscellaneous_info)):
        data[name] = run(fn, [], [""])

    contexts = {
        "both_ok": context_case(Resp(200, {"name": "Test WABA"}), Resp(200, {"data": {"application": "Ansari - Staging"}})),
        "no_name_keys": context_case(Resp(200, {}), Resp(200, {"data": {}})),
        "no_data": context_case(Resp(200, {"name": "W"}), Resp(200, {})),
        "both_failed": context_case(Resp(400, {}), Resp(400, {})),
    }

    # The menu: its lines, and what it does with each typed answer
    menu_text = []
    mod.requests = type("R", (), {"get": staticmethod(lambda url, headers=None, params=None: Resp(200, {"name": "Test WABA"}) if "debug_token" not in url else Resp(200, {"data": {"application": "Ansari - Staging"}}))})
    typed = iter(["x", "10", " 3 ", ""])
    real_input = builtins.input
    prompts = []

    def fake_input(prompt=""):
        prompts.append(prompt)
        return next(typed)

    builtins.input = fake_input
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            choice = mod.show_menu()
    finally:
        builtins.input = real_input
    menu = {"lines": out.getvalue().split("\n")[:-1], "prompts": prompts, "choice": choice}

    # The token legitimately appears in the `input_token` query of a debug_token call (Meta takes it as a GET parameter), so only
    # the PRINTED lines and the prompts count here.
    token_shown = any(TOKEN[:12] in line for run_ in data.values() for line in run_["lines"] + run_["prompts"])
    lines = [
        "// GENERATED by tests/fixtures/generate_meta_script_oracle.py from legacy/whatsapp/scripts/check_meta_subscriptions.py. Do not edit by hand.",
        "// Every screen below is what the legacy Python script printed (its real functions, a fake `requests`, scripted answers),",
        "// and every call is what it asked Meta. Regenerate with the command in the script's docstring.",
        "",
        "export interface RecordedCall {",
        "  method: string;",
        "  path: string;",
        "  query: string;",
        "  /** Whether the call carried the access token as a bearer header. */",
        "  bearer: boolean;",
        "}",
        "",
        "export interface RecordedRun {",
        "  /** What the function printed, one entry per line. */",
        "  lines: string[];",
        "  /** Each prompt it asked, in order. */",
        "  prompts: string[];",
        "  calls: RecordedCall[];",
        "}",
        "",
        f"export const FAKE_TOKEN = {json.dumps(TOKEN)};",
        "/** True when the legacy script printed (or prompted) any part of the token in these recordings. */",
        f"export const LEGACY_PRINTED_TOKEN = {json.dumps(token_shown)};",
        "",
        f"export const RUNS: Record<string, RecordedRun> = {json.dumps(data, ensure_ascii=True, indent=2)};",
        "",
        f"export const CONTEXTS: Record<string, [wabaName: string, appName: string]> = {json.dumps(contexts, ensure_ascii=True, indent=2)};",
        "",
        f"export const MENU: {{ lines: string[]; prompts: string[]; choice: string }} = {json.dumps(menu, ensure_ascii=True, indent=2)};",
        "",
    ]
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print("wrote", out_path, "|", len(data), "runs")


if __name__ == "__main__":
    main(sys.argv[1])
