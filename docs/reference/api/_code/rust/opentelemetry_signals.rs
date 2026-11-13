// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

pub fn run_example() -> Result<(), Box<dyn std::error::Error>> {
    use nemo_relay_observability::{
        otel_logs::OpenTelemetryLogConfig, otel_metrics::OpenTelemetryMetricConfig,
    };

    let logs = nemo_relay::exporter::opentelemetry_logs(OpenTelemetryLogConfig::new(
        "http://localhost:4318/v1/logs",
    ))?;
    let metrics = nemo_relay::exporter::opentelemetry_metrics(OpenTelemetryMetricConfig::new(
        "http://localhost:4318/v1/metrics",
    ))?;
    logs.register("otlp-logs")?;
    metrics.register("otlp-metrics")?;

    // Run instrumented work here.
    for diagnostic in logs.runtime_diagnostics().entries() {
        eprintln!("{}: {}", diagnostic.code, diagnostic.message);
    }

    logs.deregister("otlp-logs")?;
    logs.force_flush()?;
    logs.shutdown()?;
    metrics.deregister("otlp-metrics")?;
    metrics.force_flush()?;
    metrics.shutdown()?;
    Ok(())
}
