NOT (
            COALESCE(l.data_source, 'proxy') IN ('session_log', 'codex_session', 'gemini_session', 'opencode_session')
            AND EXISTS (
                SELECT 1
                FROM proxy_request_logs proxy_dedup
                WHERE COALESCE(proxy_dedup.data_source, 'proxy') = 'proxy'
                  AND proxy_dedup.app_type IN (l.app_type, CASE WHEN l.app_type = 'claude' THEN 'claude-desktop' ELSE l.app_type END)
                  AND proxy_dedup.status_code >= 200
                  AND proxy_dedup.status_code < 300
                  AND proxy_dedup.input_tokens = l.input_tokens
                  AND proxy_dedup.output_tokens = l.output_tokens
                  AND proxy_dedup.cache_read_tokens = l.cache_read_tokens
                  AND (
                      proxy_dedup.cache_creation_tokens = l.cache_creation_tokens
                      OR (
                          l.cache_creation_tokens = 0
                          AND COALESCE(l.data_source, 'proxy') IN ('codex_session', 'gemini_session', 'opencode_session')
                      )
                      OR (
                          proxy_dedup.cache_creation_tokens = 0
                          AND proxy_dedup.input_token_semantics = 0
                          AND COALESCE(l.data_source, 'proxy') = 'codex_session'
                      )
                  )
                  AND proxy_dedup.created_at BETWEEN
                      l.created_at - 600
                      AND l.created_at + 600
                  AND (
                      LOWER(proxy_dedup.model) = LOWER(l.model)
                      OR LOWER(proxy_dedup.model) = 'unknown'
                      OR LOWER(l.model) = 'unknown'
                  )
            )
        )
