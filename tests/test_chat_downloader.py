# SPDX-License-Identifier: MIT

# This module exercises live site integrations via each site's `_TESTS`.
# It is intentionally excluded from the default offline CI run.
import pytest

pytestmark = pytest.mark.network


from chat_downloader import ChatDownloader
from chat_downloader.sites import BaseChatDownloader, get_all_sites
from chat_downloader.sites.youtube.extractor import YouTubeChatDownloader
from tests.fixtures.youtube.extractor_tests import YOUTUBE_EXTRACTOR_TESTS

_SITE_TESTS: dict = {
    YouTubeChatDownloader: YOUTUBE_EXTRACTOR_TESTS,
}

_ALL_SITE_TESTS = [
    (site, test)
    for site in get_all_sites(True)
    for test in _SITE_TESTS.get(site, getattr(site, "_TESTS", []))
]


@pytest.mark.network
@pytest.mark.parametrize("site,test", _ALL_SITE_TESTS)
def test_site_integration(site, test) -> None:
    site_object = ChatDownloader()
    try:
        params = dict(test["params"])
        params.update(
            {
                "max_attempts": 5,
                "interruptible_retry": False,
            },
        )

        expected_result = test.get("expected_result") or {}

        if not params:
            msg = "No parameters specified."
            raise Exception(msg)  # Invalid test

        messages_list = []
        try:
            chat = site_object.get_chat(**params)

            # Ensure the site created matches the test site
            if site is not BaseChatDownloader:
                assert chat.site.__class__.__name__ == site.__name__

            messages_list = list(chat)

        except Exception as e:
            errors = expected_result.get("error")
            if not isinstance(errors, (list, tuple)):
                errors = [errors]

            correct_error = any(
                error is not None and isinstance(e, error) for error in errors
            )
            if not correct_error:
                raise

        messages_condition = expected_result.get("messages_condition")

        if messages_condition:
            if callable(messages_condition):
                assert messages_condition(messages_list)
            else:
                msg = "Message check is not callable."
                raise Exception(msg)  # Invalid test

        actual_result = {"message_types": [], "action_types": []}
        types_to_check = [
            key for key in actual_result if key in expected_result
        ]

        if types_to_check:
            for message in messages_list:
                message_type = message.get("message_type")
                if message_type not in actual_result["message_types"]:
                    actual_result["message_types"].append(message_type)

                action_type = message.get("action_type")
                if action_type not in actual_result["action_types"]:
                    actual_result["action_types"].append(action_type)

            for check in types_to_check:
                assert set(expected_result.get(check)) == set(
                    actual_result.get(check)
                )

    finally:
        site_object.close()
