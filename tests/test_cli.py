import json

from pynetscope import cli
from pynetscope.speedtest import SpeedtestResult


def test_cli_speedtest_json(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "run_speedtest",
        lambda **kwargs: SpeedtestResult("local", 1.0, 2.0, 3.0, 4, 5, 6.0, 7.0),
    )
    monkeypatch.setattr("sys.argv", ["pynetscope", "speedtest", "--json"])

    assert cli.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["download_mbps"] == 2.0


def test_cli_watch_json(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["pynetscope", "watch", "https://example.com", "--count", "1", "--json"])
    monkeypatch.setattr(cli, "_probe", lambda scope, url, timeout: scope.record("GET", url, 200, 5.0))

    assert cli.main() == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["records"][0]["status_code"] == 200
