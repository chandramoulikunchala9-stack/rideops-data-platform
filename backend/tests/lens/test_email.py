"""Email providers: SMTP (STARTTLS / implicit TLS) and Resend; account emails never break requests."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from rideops_api.main import create_app
from rideops_api.notifications import email as mail
from rideops_api.settings import LensSettings
from tests.lens.conftest import PASSWORD, unique_email

MSG = mail.Email(to="asha@example.com", subject="Verify", body="https://lens.test/verify?token=abc")


def test_smtp_starttls_login_and_message():
    sender = mail.make_sender("smtp", LensSettings(smtp_url="smtp://lens%40ex.com:p%40ss@smtp.example.com:587",
                                                   email_from="Lens <no-reply@ex.com>"))  # fmt: skip
    with mock.patch("smtplib.SMTP") as smtp:
        client = smtp.return_value  # smtplib's __enter__ returns the client itself
        sender.send(MSG)
    smtp.assert_called_once_with("smtp.example.com", 587, timeout=mail.TIMEOUT_S)
    client.starttls.assert_called_once()
    client.login.assert_called_once_with("lens@ex.com", "p@ss")
    sent = client.send_message.call_args[0][0]
    assert sent["To"] == "asha@example.com" and sent["From"] == "Lens <no-reply@ex.com>"
    assert "token=abc" in sent.get_content()


def test_smtps_implicit_tls_and_errors():
    sender = mail.make_sender("smtp", LensSettings(smtp_url="smtps://u:p@smtp.example.com"))
    with mock.patch("smtplib.SMTP_SSL") as ssl_client:
        sender.send(MSG)
    assert ssl_client.call_args[0][:2] == ("smtp.example.com", 465)
    with (
        mock.patch("smtplib.SMTP", side_effect=OSError("connection refused")),
        pytest.raises(mail.EmailError),
    ):
        mail.make_sender("smtp", LensSettings(smtp_url="smtp://u:p@h:587")).send(MSG)
    with pytest.raises(mail.EmailError):
        mail.make_sender("smtp", LensSettings(smtp_url="http://not-smtp"))
    with pytest.raises(mail.EmailError):
        mail.make_sender("resend", LensSettings())


def test_resend_http_api():
    received: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            received.append({"auth": self.headers["Authorization"],
                             "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))})  # fmt: skip
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"id": "1"}')

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        settings = LensSettings(resend_api_key="re_test", email_from="Lens <no-reply@ex.com>",
                                resend_api_url=f"http://127.0.0.1:{server.server_address[1]}/emails")  # fmt: skip
        mail.make_sender("resend", settings).send(MSG)
    finally:
        server.shutdown()
    assert received[0]["auth"] == "Bearer re_test"
    assert received[0]["body"] == {"from": "Lens <no-reply@ex.com>", "to": ["asha@example.com"], "subject": "Verify",
                                   "text": MSG.body}  # fmt: skip


def test_signup_survives_a_broken_email_provider(meta_db, lens_settings):
    class Broken:
        def send(self, email):
            raise mail.EmailError("provider down")

    app = create_app(
        db=meta_db, meta_db=meta_db, lens_settings=lens_settings, email_sender=Broken()
    )
    with (
        TestClient(app, base_url="https://testserver") as c,
        mock.patch.object(mail.logger, "exception") as logged,
    ):
        r = c.post(
            "/api/v1/auth/signup",
            json={"email": unique_email(), "password": PASSWORD, "display_name": "X"},
        )
        assert r.status_code == 201
        assert (
            c.post(
                "/api/v1/auth/password/forgot", json={"email": r.json()["user"]["email"]}
            ).status_code
            == 202
        )
        assert logged.call_count == 2 and "Could not send email" in logged.call_args[0][0]
