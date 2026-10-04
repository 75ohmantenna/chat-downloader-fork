# SPDX-License-Identifier: MIT

from __future__ import annotations

from unittest.mock import Mock

import pytest
from requests.exceptions import ConnectionError as RequestsConnectionError

from chat_downloader.sites.youtube.client_requests_bootstrap import BootstrapRequests
from tests.youtube_third_helpers import http_response


@pytest.mark.parametrize(
    "error", [RequestsConnectionError("private"), OSError("private")]
)
def test_bootstrap_connection_failures_count_actual_requests(error):
    monitor = BootstrapRequests()
    method = Mock(side_effect=[error, http_response(status_code=429), http_response()])
    with pytest.raises(type(error)):
        monitor.request(method, "https://www.youtube.com/watch?v=fixture")
    monitor.request(method, "https://www.youtube.com/watch?v=fixture")
    monitor.request(method, "https://www.youtube.com/watch?v=fixture")
    assert monitor.diagnostics["bootstrap_request_count"] == 3
    assert monitor.diagnostics["bootstrap_network_error_count"] == 1
    assert monitor.diagnostics["bootstrap_http_error_count"] == 1
    assert "private" not in str(monitor.diagnostics)
