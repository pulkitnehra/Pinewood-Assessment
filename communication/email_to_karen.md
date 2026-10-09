# Email to Karen Mills (Director of IT, Pinewood Senior Living)

**Subject:** Data platform build — source system access needed to replace the CSV handoffs

Hi Karen,

I'm building the reporting pipeline that will feed the COO's community
performance dashboard. I've validated the approach against the six months of
CSV exports your team shared, so the next step is moving off manual file
drops and onto direct, scheduled extracts. Here's the access I need, in
priority order:

**1. PointClickCare** — read-only API credentials (or a reporting database
login) covering residents, incidents, and care level history for all 14
communities. If API access needs a PCC marketplace request, I can supply the
technical details for the form.

**2. Yardi Senior Living** — read-only access to unit inventory and lease
data. A scheduled report export to SFTP works fine if direct database access
isn't an option; I'd just ask that the export schema stays fixed.

**3. ADP** — read-only API client (Workforce Now reporting API) for shifts,
roles, and pay rates. Payroll detail beyond hourly rate isn't needed, so a
scoped permission set keeps this low-risk.

**4. HubSpot** — a private app token with read scope on contacts/deals for
the sales funnel data.

**5. Google Business Profile** — viewer access to the Business Profile
account (or an API service account) for review data.

Three requests that aren't credentials:

- **A service account, not a personal login**, for anything scheduled — so
  jobs don't break when someone changes a password.
- **Community master data.** No system I've seen holds the authoritative
  list of communities with their states/regions. Today I've had to assume
  the mapping, and regional reporting (including who's allowed to see what)
  depends on it being right. Even a confirmed spreadsheet would do for now.
- **A data contact per system** for the handful of quality questions I've
  logged — for example, resident records that appear in two communities at
  once, and unit records coded to communities that don't exist (C905, C934,
  C936, C951, C969 — possibly test data).

Happy to jump on a call if that's easier, and I can work through whatever
security review process you need for each system — just point me at the
forms.

Thanks,
[Name]
