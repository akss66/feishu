from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "scripts" / "run-commerce-agent.ps1"
INSTALLER = ROOT / "scripts" / "install-commerce-agent-autostart.ps1"


def test_runner_enables_the_first_pass_runtime_without_secret_file_operations() -> None:
    text = RUNNER.read_text(encoding="utf-8")

    expected_assignments = {
        "INGESTION_DNS_MODE": "cloudflare_doh",
        "INGESTION_SCHEDULER_ENABLED": "true",
        "INTELLIGENCE_ANALYSIS_ENABLED": "true",
        "INTELLIGENCE_DAILY_REPORT_ENABLED": "true",
        "INTELLIGENCE_ALERTS_ENABLED": "true",
        "INTELLIGENCE_QA_ENABLED": "true",
        "DEEPSEEK_TIMEOUT_SECONDS": "60",
        "LOG_LEVEL": "INFO",
    }
    for name, value in expected_assignments.items():
        assert f'$env:{name} = "{value}"' in text

    for forbidden in ("Get-Content", "Set-Content", "Copy-Item", "Remove-Item"):
        assert forbidden not in text
    assert "-m commerce_agent" in text
    assert "data\\runtime" in text
    assert '$ErrorActionPreference = "Continue"' in text
    assert "$agentExitCode = $LASTEXITCODE" in text


def test_installer_registers_a_non_elevated_single_instance_restartable_task() -> None:
    text = INSTALLER.read_text(encoding="utf-8")

    assert '"CrossBorderCommerceAgent"' in text
    assert "New-ScheduledTaskTrigger -AtLogOn" in text
    assert "New-ScheduledTaskPrincipal" in text
    assert "-ExecutionPolicy RemoteSigned" in text
    assert '-RunLevel "Limited"' in text
    assert '-MultipleInstances "IgnoreNew"' in text
    assert "-RestartCount 10" in text
    assert "-RestartInterval (New-TimeSpan -Minutes 1)" in text
    assert "Register-ScheduledTask" in text
    assert "Start-ScheduledTask" in text
    assert "Unregister-ScheduledTask" not in text
    assert "-Force" not in text


def test_installer_refuses_to_replace_an_unknown_same_named_task() -> None:
    text = INSTALLER.read_text(encoding="utf-8")

    assert "Get-ScheduledTask" in text
    assert "existing task is not managed by this project" in text
    assert "Set-ScheduledTask" in text
