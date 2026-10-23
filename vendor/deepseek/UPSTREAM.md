# DeepSeek Harness source adaptation

Source: https://github.com/deepseek-ai/deepseek-harness/tree/5badb15009ae1756c3afe0ae0cef1faafc290ccc

License: MIT. DeepSeek copyright and Cordis/Shigma copyright are retained in `upstream/LICENSE` and `upstream/vendor/cordis/LICENSE`. `manifest.json` pins every retained source digest.

`chaos/plugins.py` ports the dependency-waiting `missingServices` check and failure-cleanup contract from `packages/extensions/cordis-host-runner/src/lifecycle.ts`, plus Cordis fiber states, effect ownership, reverse disposal and dependency reactivation from `vendor/cordis/src/fiber.ts`, into the Python runtime. This is a deliberately smaller synchronous host contract; it does not claim binary compatibility with arbitrary DSH JavaScript plugins. Registered host plugins can expose any trusted tool/provider as a service. Remote configuration can enable registered plugins but cannot import arbitrary Python modules.

`web/dsh-state.js` adapts the distinct-call aggregation, stable count ranking and latest-running selection in `process-activity.ts`; the independent elapsed-time clock and cleanup in `RunningStatus.tsx`; and the host/client package-run identity check in `status.ts`. React is replaced by a small DOM API. The upstream tool-name classifier and argument-key heuristic were intentionally removed: categories/details are explicit typed event fields, and task meaning remains model/tool protocol data. Private model reasoning is not fabricated or exposed; the UI receives action summaries, tool state and final outputs.

The `upstream/` directory contains reference source, not a second running daemon. Chaos runs the adapted code directly. Full DSH package installation, its JavaScript evaluator, and ecosystem-wide compatibility are outside this adapter.
