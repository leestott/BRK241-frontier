# Security reporting

FibreOps is a reference demo maintained in this repository, not an
official Microsoft product or a supported Microsoft service. Please do not
post vulnerabilities, credentials or exploit details in public issues or
pull requests. Contact the repository owner privately through their
[GitHub profile](https://github.com/leestott) before sharing details; if
private vulnerability reporting is enabled in the future, use the
repository's **Report a vulnerability** link instead.

Include the affected revision and file, reproduction steps, impact and any
configuration needed to reproduce the issue. Do not include real tenant
credentials or customer data in a report.

For a vulnerability in a **Microsoft product or service** rather than in
this reference demo, use the [Microsoft Security Response Center](https://msrc.microsoft.com/create-report).

## Public-sharing checklist

- Publish only tracked source and deliberately reviewed new files. Never include
  `.env`, `.azure/`, virtual environments, runtime `state/`, authentication caches,
  customer recordings or downloaded evaluation results.
- Scan reachable Git history and the proposed public working-tree files with a
  local secret scanner, using redacted output. A history-only scan does not cover
  uncommitted changes. Keep scan reports outside the repository.
- Keep GitHub secret scanning and push protection enabled where supported, and
  review dependency alerts. A clean scan is evidence, not a guarantee.
- Review screenshots, diagrams and sample datasets for personal/customer data.
  The application datasets are demo fixtures, not a representation of a live
  operator inventory. Never substitute customer data in a public example.
- Preserve license notices and verify rights to contributed code, diagrams,
  recordings and brand assets. Automated scans cannot establish provenance.
- Keep the deployed console behind Entra sign-in. Its action endpoints can invoke
  external systems; this reference demo is not a hardened multi-tenant service.
- State which integrations are mocks, optional or preview services. Do not
  equate an evaluation run's `Completed` status with passing quality gates.
