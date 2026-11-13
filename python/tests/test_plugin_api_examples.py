# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Execute the Python source displayed in plugin API documentation."""

from __future__ import annotations

import asyncio
import io
import os
import re
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
OBSERVABILITY = "docs/reference/api/observability.mdx"
ADAPTIVE = "docs/reference/api/adaptive.mdx"
RESOURCE_METRICS = "docs/configure-plugins/resource-metrics.mdx"
EXAMPLES = {
    "atif": (OBSERVABILITY, ("Agent Trajectory Interchange Format (ATIF)", "Manual API")),
    "atof": (OBSERVABILITY, ("Agent Trajectory Observability Format (ATOF)", "Manual API")),
    "atof_diagnostics": (OBSERVABILITY, ("Agent Trajectory Observability Format (ATOF)", "Runtime Diagnostics")),
    "opentelemetry": (OBSERVABILITY, ("OpenTelemetry", "Direct Subscribers")),
    "opentelemetry_signals": (
        OBSERVABILITY,
        ("OpenTelemetry", "Direct Subscribers", "Log and Metric Subscriber Lifecycle"),
    ),
    "openinference": (OBSERVABILITY, ("OpenInference", "Direct Subscriber")),
    "adaptive_configuration": (ADAPTIVE, ("Adaptive Configuration", "Manual API")),
    "acg": (ADAPTIVE, ("Adaptive Cache Governor (ACG)", "Manual API")),
    "adaptive_hints": (ADAPTIVE, ("Adaptive Hints", "Manual API")),
    "response_cache": (ADAPTIVE, ("Response Cache", "Manual API")),
    "resource_metrics": (RESOURCE_METRICS, ("Resource Metrics", "Collect One Snapshot")),
}
RUST_SOURCES = {
    OBSERVABILITY: {
        "atif_exporter",
        "atof_exporter",
        "atof_diagnostics",
        "opentelemetry_subscriber",
        "opentelemetry_signals",
        "openinference_subscriber",
    },
    ADAPTIVE: {"adaptive_configuration", "acg", "adaptive_hints", "response_cache"},
    RESOURCE_METRICS: {"resource_metrics"},
}


def fenced_examples(page: Path) -> list[tuple[tuple[str, ...], str, str]]:
    """Identify exact fenced source by its full heading hierarchy and language."""
    hierarchy: list[tuple[int, str]] = []
    examples = []
    language: str | None = None
    body: list[str] = []
    for line in page.read_text(encoding="utf-8").splitlines():
        if language is not None:
            if line == "```":
                examples.append((tuple(title for _, title in hierarchy), language, "\n".join(body) + "\n"))
                language = None
            else:
                body.append(line)
        elif line.startswith("```"):
            language = line[3:].split()[0]
            body = []
        elif match := re.match(r"^(#{1,6}) (.+)$", line):
            level = len(match[1])
            hierarchy = [(depth, title) for depth, title in hierarchy if depth < level]
            hierarchy.append((level, match[2]))
    assert language is None, f"Unclosed code fence in {page}"
    return examples


def displayed_source(name: str) -> str:
    filename, hierarchy = EXAMPLES[name]
    matches = [
        source
        for headings, language, source in fenced_examples(REPOSITORY_ROOT / filename)
        if headings == hierarchy and language == "python"
    ]
    assert len(matches) == 1, (filename, hierarchy, len(matches))
    return matches[0]


def test_displayed_source_inventory_has_registered_coverage() -> None:
    for filename in RUST_SOURCES:
        page = REPOSITORY_ROOT / filename
        actual = {headings for headings, language, _ in fenced_examples(page) if language == "python"}
        expected = {hierarchy for path, hierarchy in EXAMPLES.values() if path == filename}
        assert actual == expected, filename
        assert not any(language == "rust" for _, language, _ in fenced_examples(page)), filename
        references = re.findall(r'<Code\s+src="(_code/rust/[^\"]+\.rs)"', page.read_text())
        for source in re.findall(r'<Code\s+src="([^"]+)"', page.read_text()):
            if Path(source).suffix in {".py", ".js", ".mjs", ".cjs", ".ts", ".rs"}:
                assert source in references, f"Unregistered source component in {filename}: {source}"
        assert {Path(reference).stem for reference in references} == RUST_SOURCES[filename]
        assert len(references) == len(RUST_SOURCES[filename])
        for reference in references:
            assert (page.parent / reference).is_file()
    reference_pages = sorted((REPOSITORY_ROOT / "docs/reference/api").glob("*.mdx"))
    for page in reference_pages:
        if page.name in {"observability.mdx", "adaptive.mdx"}:
            continue
        assert not any(
            language in {"python", "js", "javascript", "ts", "typescript", "rust"}
            for _, language, _ in fenced_examples(page)
        ), page
    directories: dict[Path, set[Path]] = {}
    for filename, names in RUST_SOURCES.items():
        directory = REPOSITORY_ROOT / Path(filename).parent / "_code/rust"
        directories.setdefault(directory, set()).update(directory / f"{name}.rs" for name in names)
    for directory, expected_paths in directories.items():
        assert set(directory.glob("*.rs")) == expected_paths
    included_sources = set()
    for test_path in [
        "crates/sdk/tests/documentation_examples.rs",
        "crates/adaptive/tests/integration/runtime_integration_tests.rs",
    ]:
        test_file = REPOSITORY_ROOT / test_path
        for source in re.findall(r'include!\("([^"]+)"\)', test_file.read_text()):
            included_sources.add((test_file.parent / source).resolve())
    displayed_sources = {source.resolve() for paths in directories.values() for source in paths}
    assert displayed_sources <= included_sources


@pytest.mark.parametrize("name", EXAMPLES)
def test_displayed_python_example_matches_its_outcome(name: str) -> None:
    environment = dict(os.environ)
    environment["OTEL_AUTHORIZATION"] = "Bearer documentation-test"
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), name],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("name", ["opentelemetry", "openinference"])
@pytest.mark.parametrize("authorization", [None, " invalid "])
def test_displayed_constructor_rejects_missing_or_invalid_header(name: str, authorization: str | None) -> None:
    environment = dict(os.environ)
    environment.pop("OTEL_AUTHORIZATION", None)
    if authorization is not None:
        environment["OTEL_AUTHORIZATION"] = authorization
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), name],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode != 0
    assert "OTEL_AUTHORIZATION" in result.stderr


def exercise_example(name: str) -> None:
    import nemo_relay
    from nemo_relay.runtime_registrations import list_runtime_registrations

    before = list_runtime_registrations()
    printed_values: list[Any] = []
    output = io.StringIO()

    def observe_print(*values: Any, **options: Any) -> None:
        printed_values.extend(values)
        print(*values, **options)

    namespace: dict[str, Any] = {"__name__": "__main__", "print": observe_print}
    with tempfile.TemporaryDirectory() as directory:
        diagnostic_exporter = None
        if name == "atof_diagnostics":
            configuration = nemo_relay.AtofExporterConfig()
            configuration.output_directory = directory
            configuration.filename = "diagnostics.jsonl"
            diagnostic_exporter = nemo_relay.AtofExporter(configuration)
            namespace["exporter"] = diagnostic_exporter
        try:
            with redirect_stdout(output):
                exec(compile(displayed_source(name), EXAMPLES[name][0], "exec"), namespace)
            if name == "atif":
                trajectory = namespace["trajectory"]
                assert trajectory["session_id"] == "session-1"
                assert trajectory["agent"]["name"] == "agent"
                assert trajectory["steps"] == []
                exporter = namespace["exporter"]
                assert isinstance(exporter, nemo_relay.AtifExporter)
                assert exporter.export()["steps"] == []
            elif name in {"atof", "atof_diagnostics"}:
                assert namespace["exporter"].runtime_diagnostics().entries == []
                assert output.getvalue() == ""
            elif name in {"opentelemetry", "openinference"}:
                assert namespace["subscriber"].runtime_diagnostics().entries == []
                assert output.getvalue() == ""
            elif name == "opentelemetry_signals":
                assert namespace["logs"].runtime_diagnostics().entries == []
                assert output.getvalue() == ""
            elif name == "resource_metrics":
                snapshot = printed_values[0]
                assert isinstance(snapshot, nemo_relay.resource_metrics.ResourceMetricsSnapshot)
                assert isinstance(snapshot.timestamp, datetime) and snapshot.timestamp.tzinfo is not None
                assert snapshot.measurement_scope == "process_tree"
                assert snapshot.cpu is not None and snapshot.cpu.consumption_rate is None

                async def collect_after_close() -> None:
                    with pytest.raises(RuntimeError, match="requires an active resource_metrics component"):
                        await nemo_relay.resource_metrics.collect()

                asyncio.run(collect_after_close())
            else:
                assert output.getvalue() == ""
                # The public error confirms shutdown completed, rather than merely
                # checking that registration once succeeded.
                if "runtime" in namespace:
                    with pytest.raises(RuntimeError, match="already shut down"):
                        namespace["runtime"].deregister()
        finally:
            if name in {"opentelemetry", "openinference"} and "subscriber" in namespace:
                namespace["subscriber"].shutdown()
            if diagnostic_exporter is not None:
                diagnostic_exporter.shutdown()
    assert list_runtime_registrations() == before


if __name__ == "__main__":
    exercise_example(sys.argv[1])
