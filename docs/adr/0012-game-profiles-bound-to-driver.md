# Game profiles remember the driver, and volume is only restored on the same driver

Game profiles store raw volume (ADR-0009), which means different loudness on different receivers and protocols. Each profile therefore records the driver key it was saved with. On launch, the volume is only applied when the current driver matches. A mismatched volume is skipped, not converted, because an unexpectedly loud jump is worse than no change. Listening-mode codes are also driver-specific, so the same rule applies to them.
