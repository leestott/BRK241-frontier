# Contributing to FibreOps

This repo is a **reference demo** for the BRK241 session. It is published
under the MIT license and we welcome small fixes (typos, broken links, demo
ergonomics). It is intentionally **not** a supported product.

## Contributor License Agreement

This project welcomes contributions and suggestions. Most contributions require
you to agree to a Contributor License Agreement (CLA) declaring that you have
the right to, and actually do, grant us the rights to use your contribution.
For details, visit <https://cla.opensource.microsoft.com>.

When you submit a pull request, a CLA bot will automatically determine whether
you need to provide a CLA and decorate the PR appropriately (e.g., status check,
comment). Simply follow the instructions provided by the bot. You will only need
to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](CODE_OF_CONDUCT.md).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/)
or contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional
questions or comments.

## Development workflow

```powershell
git clone <your-fork-url>
cd BRK241-frontier
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m pytest -q
```

The demo can be run **without any Azure credentials** thanks to the
deterministic `LocalAgent` fallback:

```powershell
.\.venv\Scripts\python.exe -m fibreops.demo --signals 3
```

## What we accept

- Doc fixes (README, DEMO.md, KQL.md)
- Bug fixes that preserve the existing tests
- Additional rubric criteria for the optimiser
- Additional KQL queries

## What we do not accept

- Changes that require new managed services to make the demo work
- Changes that remove the local-agent fallback
- Changes that bake in tenant-specific configuration
- Anything that adds a dependency on a non-public preview SDK without a fallback

## Trademarks

This project may contain trademarks or logos for projects, products, or services.
Authorised use of Microsoft trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must
not cause confusion or imply Microsoft sponsorship. Any use of third-party
trademarks or logos are subject to those third-party's policies.
