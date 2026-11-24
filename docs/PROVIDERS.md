# Provider guide

Use a supported adapter, an exact model ID available to your account, and credentials when required. A brand name such as “Claude” or “Codex” is not a universal model ID.

## Secrets

Copy `.env.example` to `.env` and fill only the keys you use. It is gitignored. The CLI does not automatically load it. In your terminal:

```bash
set -a
source .env
set +a
```

Keys are authentication headers, never task text, and are excluded from saved configuration and published experiment records. `--api-key-env MY_KEY` takes a variable name, not the secret value.

## Supported API contracts

| Adapter | Default API root | Request route | Default key variable |
|---|---|---|---|
| `ollama` | `http://localhost:11434` | `/api/chat` | None |
| `anthropic` | `https://api.anthropic.com/v1` | `/messages` | `ANTHROPIC_API_KEY` |
| `responses` | `https://api.openai.com/v1` | `/responses` | `OPENAI_API_KEY` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta` | `/models/{model}:generateContent` | `GEMINI_API_KEY` |
| `chat` | Explicit `--base-url` required | `/chat/completions` | `RULESHIFT_API_KEY` |

Base URLs stop at the API root. Custom endpoints must implement the selected contract. Redirects are rejected rather than forwarding credentials.

```bash
python3 -m ruleshift check --provider anthropic --model YOUR_CLAUDE_MODEL_ID
python3 -m ruleshift run --provider anthropic --model YOUR_CLAUDE_MODEL_ID --suite policy --seeds 10 --repeats 3

python3 -m ruleshift check --provider responses --model YOUR_OPENAI_MODEL_ID --omit-temperature
python3 -m ruleshift run --provider responses --model YOUR_OPENAI_MODEL_ID --omit-temperature --suite policy --seeds 10 --repeats 3

python3 -m ruleshift check --provider gemini --model YOUR_GEMINI_MODEL_ID
python3 -m ruleshift run --provider gemini --model YOUR_GEMINI_MODEL_ID --suite policy --seeds 10 --repeats 3

python3 -m ruleshift check --provider chat --model YOUR_MODEL_ID --base-url http://localhost:1234/v1
python3 -m ruleshift run --provider chat --model YOUR_MODEL_ID --base-url https://YOUR_GATEWAY/v1 --api-key-env MY_GATEWAY_KEY --suite policy --seeds 10 --repeats 3
```

A standard unauthenticated local compatible server can run without a key.

## Settings and responses

Every request is a fresh conversation without tools/retrieval. Expected output is exactly `{"answer": NUMBER}` or `{"answer": true/false}`. JSON code fences are accepted; arbitrary prose and strings such as `"true"` are not silently reinterpreted.

Defaults: temperature 0, 512 output tokens, 60-second timeout. A reasoning model may require a larger `--max-tokens`. If a model rejects temperature, explicitly use `--omit-temperature`; the omission is recorded and affects comparison eligibility. There are no automatic parameter changes or provider fallbacks.

Requested limits map to `max_output_tokens` (Responses), `max_tokens` (Anthropic/compatible Chat), `maxOutputTokens` (Gemini), and `num_predict` (Ollama). These do not imply identical hidden reasoning or token accounting. Adapters extract final text blocks, not private thinking blocks.

## Compatibility versus quality

`check` makes one call and reports endpoint reachability, valid response format and answer correctness separately. Missing native-provider keys stop a command before creating a run. Connection, API-shape and output-format failures do not prove a model cannot reason.

This MVP calls model APIs. It does not automate Codex CLI/Claude Code, reuse their login sessions or evaluate tool execution. Bedrock/Vertex/Azure-specific authentication and multimodal inputs require further adapters/harnesses.

Official references: [Ollama Chat](https://docs.ollama.com/api/chat), [Claude Messages](https://platform.claude.com/docs/en/api/messages/create), [OpenAI Responses](https://developers.openai.com/api/reference/resources/responses/methods/create), [Gemini generateContent](https://ai.google.dev/api/generate-content).
