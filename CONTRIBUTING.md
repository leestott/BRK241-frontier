# Contributing to FibreOps

This repo is a **reference demo** for the BRK241 session. It is published
under the MIT license and we welcome small fixes (typos, broken links, demo
ergonomics). It is intentionally **not** a supported product.

## Contribution rights

Submit only material you have the right to contribute under the repository's
[license](LICENSE), and retain existing copyright notices. This repository does
not currently configure a CLA bot. If an additional agreement is required, the
maintainer must explain that requirement before accepting the contribution.

This project has adopted the [Microsoft Open Source Code of Conduct](CODE_OF_CONDUCT.md).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/)
or contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional
questions or comments.

## Development workflow

Start with the [reproduction guide](docs/REPRODUCING.md); it distinguishes the
current portable release from historical experiments and the separate public
voice candidate branch, and keeps evaluation SDKs isolated from the application
runtime. Do not merge experimental
voice definitions or UI changes into this branch merely to reproduce a benchmark.

Use Git, PowerShell 7, Python 3.11 or 3.12 (the Windows/Linux CI matrix),
and a current Node.js LTS release for browser-client tests. A clean-clone local
rehearsal was also validated with Python 3.13. Azure CLI, Docker and Azure
credentials are not required for the offline contributor workflow.
Start in a fresh terminal without inherited integration settings. Do not copy
an existing `.env`, `.azure/`, or runtime `state/` into the checkout, and do not
copy `.env.example` for an offline rehearsal.

```powershell
git clone <your-fork-url>
cd BRK241-frontier
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e . --no-deps
.\.venv\Scripts\python.exe -m pip check
$env:FIBREOPS_AGENT_BACKEND = "local"
.\.venv\Scripts\python.exe -m pytest -q
node --test tests\test_voice_client.cjs
```

The default environment can skip optional Evaluation SDK and Voice Agents
Preview tests. Those SDKs must stay separate from the application environment;
CI runs the isolated checks. See [the test inventory](tests/README.md) and
[voice evaluation setup](README.md#azure-ai-evaluation-sdk-and-foundry-results).
Do not install the preview publisher SDK into `.venv` just to remove skips.

The demo can be run **without any Azure credentials** thanks to the
deterministic `LocalAgent` fallback. Use a clean local environment without real
integration settings (see the reproduction guide); selecting `local` alone
does not disable an existing Teams webhook:

```powershell
.\.venv\Scripts\python.exe -m fibreops.demo run --backend local --signals 3
```

The command creates synthetic incident records and local outboxes under ignored
`state/`. To view them, run the local UI and open <http://127.0.0.1:8800/>:

```powershell
.\.venv\Scripts\python.exe -m fibreops.demo ui
```

The UI shares this checkout's local state. Do not send notifications or inject
incidents into an existing cloud deployment as a contributor smoke test.

## What we accept

- Doc fixes (README, DEMO.md, KQL.md)
- Bug fixes that preserve the existing tests
- Additional rubric criteria for the optimiser
- Additional KQL queries

## What we do not accept

- Changes that require new managed services to make the demo work
- Changes that remove the local-agent fallback
- Changes that bake in tenant-specific configuration
- Preview SDK dependencies without an isolated installation and an offline fallback

## Trademarks

This project may contain trademarks or logos for projects, products, or services.
Authorised use of Microsoft trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must
not cause confusion or imply Microsoft sponsorship. Any use of third-party
trademarks or logos are subject to those third-party's policies.
