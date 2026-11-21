# County project discovery

The `BBA County Discovery` workflow runs daily at 10:30 UTC and can be run manually. Separate St Johns and Flagler jobs isolate failures. Existing roads/rail/long-range workflows continue unchanged. No app bundle, production projects.json or existing pin is edited.

## Intake

`config/counties.json` registers state, county FIPS, authorities, field mappings, official context pages, local news publishers and search queries. St Johns enumerates CGCP (clearing/grading), PREAPP, MODCP, MDP, MAJMOD, PUD and REZ current/previous years plus a rotating historic year back to 1998. ASP.NET year selection is confirmed; a paging change fails visibly. Up to 60 detail pages per run extract project descriptions and explicit related application links. Contact tables are excluded. Flagler intake covers the official county development polygons and Palm Coast planning points and capital-project polygons, with complete object-ID/batch reconciliation.

Google News RSS and Bing public search RSS discover supplemental reports, developer/agency leads and publicly indexed Reddit/Facebook-group/Nextdoor pages. This is search-index coverage, not direct/private group access. No account, group membership, posting or scraping behind sign-in is performed. Some searches produce no results; that does not prove no project exists. Agency/developer discovery is supplemental search and official-page link extraction, not full permit-register integration for FDEP, SJRWMD or federal systems. Existing state/federal road adapters remain in the separate daily research workflow.

## Evidence and location

Authority/type/record identities deduplicate repeated observations. Separate applications/phases and multiple GIS features for one application remain distinct. Name or record-number matches suggest corroboration and never merge records or override status/coordinates automatically. An explicit parent link is preserved as a relationship, not interpreted as the same construction footprint.

Review artifacts retain source URLs, timestamps, official status fields, unknown timing, confidence, source failures and known gaps. Permit approval/closure, fiscal years and phase completion do not establish completed construction or an announced start year. Community/news claims remain unverified leads until checked against official records. Polygon interior points (including holes/disconnected polygons) and official project points are checked against the matching Census TIGER county boundary. These are **pin candidates**, not publication approvals: exact project/subphase/description agreement still needs review. A broader PUD boundary must not locate a smaller fire-station parcel.

## Outputs and history

Download `county-discovery-FL-12109` and `county-discovery-FL-12035` Actions artifacts. Each contains `.review.json` and `.pin-candidates.json`. Every candidate has `publication_eligible: false`; these files do not update phone/TestFlight map pins. State cache rotates historic years/details and retains previously observed official records. Prior observations absent from the current snapshot are labeled historical observations, not canceled/completed. GitHub caches are evictable, so this is operational continuity, not a permanent archival database; retain artifacts externally for durable history. Artifacts are retained 30 days. Core intake failures fail the job while preserving review outputs; blocked optional pages remain visible as partial coverage.

## Expand to another county/state

1. Add a county entry with state, county GEOID, verified authority services, field mappings and local sources. Verify the service's ownership through the official government site; similarly named ArcGIS services are not sufficient.
2. Add its ID to the workflow matrix. Add a portal adapter and fixtures if its application system differs; a St Johns portal is not assumed nationwide.
3. Run fixtures and live ID reconciliation, county-boundary checks and description/phase review. Include relevant municipal authorities and report unimplemented coverage explicitly.
4. Approve verified project rows through a separate publication step and extend the app's county-aware loader/trust rules before shipping pins. The current beta loader is St Johns-only. Do not bypass those bounds or change the production feed as a side effect of research.

Run locally:

```
python -m pip install -r requirements.txt
python -m unittest discover -s scripts -p 'test_bba_county_discovery.py'
python scripts/bba_county_discovery.py --county FL-12035
```

Regression: CGCP2025000003 Twin Creeks Fire Station must be discovered from the 2025 clearing register, not a hand-entered pin. Keep it unlocated until its dedicated site geometry is verified. Flagler is an initial three-source pilot, not a claim of complete county/municipal permit coverage.
