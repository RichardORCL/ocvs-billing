# OCVS Billing overview

## Overview

`getbilling.py` is a CLI utility that builds an **OCI VMware Solution (OCVS) ESXi host billing overview** for your tenancy: it discovers ESXi hosts, resolves related SDDC names, and surfaces commitment and contract dates so you can see what is committed and when terms end.

### Prerequisites

- Python environment with the project dependencies installed (including `oci` and `requests`).
- OCI Python SDK **at least version `2.164.0`** (the script checks this at startup).
- Valid OCI authentication (API key config file is typical). The script can also use instance principals (`-ip`) or delegation token (`-dt`); in Cloud Shell it defaults to delegation token when `OCI_CLI_CLOUD_SHELL=true`.

### Running the script

Easiest way to run this script is from the OCI Cloud Shell:
```
git clone https://github.com/RichardORCL/OCVS-Billing.git
cd OCVS-Billing
python getbilling.py
```

You can also run it from any other place where you have the OCI CLI configured.

```bash
python getbilling.py [-cp PROFILE] [-ip] [-dt] [-log [FILE]]
```

| Flag | Meaning |
|------|---------|
| `-cp` | Config profile name (default: `DEFAULT`). |
| `-ip` | Authenticate with instance principals. |
| `-dt` | Authenticate with a delegation token (expects `OCI_CONFIG_FILE`, `OCI_CONFIG_PROFILE`, and `delegation_token_file` in config). |
| `-log` | Mirror stdout to a log file (defaults to `log.txt` if the filename is omitted). |

After startup the script:

1. Parses CLI options and builds an OCI config + signer (`create_signer`).
2. **Logs in** and loads compartments (`Login`, shared with other tools under `ocimodules`).
3. Asks whether to scan **only the configured region** (press Enter) or **all subscribed regions** (type `all`).

### What it collects

For **each selected region** (by temporarily setting `config["region"]`):

1. **Billing donor hosts** — For every compartment, it calls `EsxiHostClient.list_esxi_hosts` with `is_billing_donors_only=True`. These are unused billing terms (“donors”) and are reported in a separate **Donor Host Details** table. If the service returns **404** for the region, that region is skipped for the rest of the scan.
2. **All ESXi hosts** — It runs a **Resource Search** structured query `query vmwareesxihost resources`, then for each hit calls `get_esxi_host` to fetch full host detail. Those hosts drive the main **ESXi Host Billing** table.

**SDDC names** are resolved per host via `sddc_id`: a cached helper switches the client to the region encoded in the SDDC OCID and calls `SddcClient.get_sddc`, so SDDCs in other regions than the current loop iteration still resolve correctly.

**Region** shown in the tables is taken from the host OCID (fourth dot-separated segment), not only from the loop variable.

### Output

- **ESXi Host Billing Table** — Columns include region, compartment path, host name, SDDC name, lifecycle state, shape, OCPU count, creation date, age in days, current/next commitment, contract end date, and days until contract end. Billing fields are read from the host object and, when present, from `billing_term_info`.
- **Donor Host Details** — Summary for donor-only hosts (region, compartment, hostname, shape, OCPU, commitment, contract end, days left).

Tables are printed to the console. When a table has a save name, the script also writes **`esxi_host_billing_YYYYMMDD_HHMMSS.csv`** and **`esxi_donor_hosts_YYYYMMDD_HHMMSS.csv`** in the current working directory.

### Implementation notes

- OCI’s circuit breaker is disabled at import (`NoCircuitBreakerStrategy`) for predictable SDK behavior in this script.
- Application version string is embedded in the script as `application_version` (for human reference; use `oci.__version__` or your deployment tagging for SDK/runtime versioning).

