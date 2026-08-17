from scripts.build_standalone import build_command, main


def test_build_command_uses_clean_noninteractive_output_paths(tmp_path):
    command = build_command(tmp_path, executable="/python")
    assert command == [
        "/python",
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath",
        str(tmp_path / "dist" / "standalone"),
        "--workpath",
        str(tmp_path / "build" / "pyinstaller"),
        str(tmp_path / "cluesb.spec"),
    ]


def test_driver_rejects_non_macos_without_invoking_builder(tmp_path, capsys):
    called = False

    def runner(command):
        nonlocal called
        called = True
        return 0

    result = main(root=tmp_path, system=lambda: "Linux", machine=lambda: "x86_64", runner=runner)
    assert result == 2
    assert called is False
    assert "macOS" in capsys.readouterr().err


def test_driver_reports_artifact_and_architecture_after_success(tmp_path, capsys):
    artifact = tmp_path / "dist" / "standalone" / "cluesb"

    def runner(command):
        artifact.parent.mkdir(parents=True)
        artifact.touch()
        return 0

    result = main(root=tmp_path, system=lambda: "Darwin", machine=lambda: "arm64", runner=runner)
    assert result == 0
    output = capsys.readouterr().out
    assert str(artifact) in output
    assert "arm64" in output
