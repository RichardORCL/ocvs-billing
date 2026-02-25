import sys
import time
import csv
from datetime import date, datetime
import oci
import requests
import re

from ocimodules.functions import input_command_line, create_signer, check_oci_version, MyWriter
from ocimodules.IAM import GetCompartments, Login, SubscribedRegions, GetHomeRegion, GetCompartmentFullPath

# Disable OCI CircuitBreaker feature
oci.circuit_breaker.NoCircuitBreakerStrategy()

#################################################
#           Application Configuration           #
#################################################
min_version_required = "2.164.0"
application_version = "25.02.2026"


############################################
# functions
############################################

def region_from_ocid(ocid):
    """
    Extract region from an OCID.
    OCID format: ocid1.<resource>.<realm>.<region>.<unique_id>
    """
    if not ocid:
        return ""
    parts = ocid.split(".")
    if len(parts) >= 4:
        return parts[3]
    m = re.search(r"ocid1\.\w+\.\w+\.(.+?)\.", ocid)
    return m.group(1) if m else ""


def GetSDDCByOCID(config, signer):
    """
    Returns a function that looks up an SDDC by its OCID.
    Uses an in-memory cache to avoid redundant lookups.
    Automatically adjusts config['region'] based on OCID.
    """

    cache = {}

    def extract_region_from_ocid(ocid):
        """
        OCID format: ocid1.<resource>.<realm>.<region>.<unique_id>
        Example: ocid1.sddc.oc1.eu-frankfurt-1.<unique_id>
        """
        parts = ocid.split(".")
        if len(parts) >= 4:
            return parts[3]
        # fallback for non-standard formats
        m = re.search(r"ocid1\.\w+\.\w+\.(.+?)\.", ocid)
        if m:
            return m.group(1)
        return None

    def lookup(sddc_ocid):
        if sddc_ocid in cache:
            return cache[sddc_ocid]
        region = extract_region_from_ocid(sddc_ocid)
        if region:
            config["region"] = region
        ocvp = oci.ocvp.SddcClient(config, signer=signer)
        try:
            sddc = ocvp.get_sddc(sddc_ocid).data
            cache[sddc_ocid] = sddc
            return sddc
        except Exception as e:
            print(f"Error retrieving SDDC for OCID {sddc_ocid}: {e}")
            return None

    return lookup

def print_table(headers, rows, table_name=None):
    """Print a text table without external dependencies. If table_name is provided, also save the table as a CSV file (filename: table_name_YYYYMMDD_HHMMSS.csv)."""
    if not rows:
        print("No data to display.")
        return
    col_widths = [
        max(len(str(h)), max(len(str(row[i])) for row in rows))
        for i, h in enumerate(headers)
    ]
    col_widths = [min(w, 40) for w in col_widths]
    fmt = "  ".join(f"{{:<{w}}}" for w in col_widths)
    print(fmt.format(*headers))
    print("-" * (sum(col_widths) + 2 * (len(headers) - 1)))
    for row in rows:
        print(fmt.format(*[str(x) for x in row]))

    if table_name:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_filename = f"{table_name}_{timestamp}.csv"
        try:
            with open(csv_filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                writer.writerows(rows)
            print(f"\nTable saved to {csv_filename}")
        except Exception as e:
            print(f"Error saving table to CSV: {e}")



##########################################################################
# Main Program
##########################################################################

print ("OCI - OCVS Billing Overview")
print ("This utility help you get an overview of all ESXi hosts and their billing cycle information")
print ("============================================================================================")
print ("")

check_oci_version(min_version_required)

# Check command line parameters
cmd = input_command_line()

# if logging to file, overwrite default print function to also write to file
if cmd.log_file != "":
    writer = MyWriter(sys.stdout, cmd.log_file)
    sys.stdout = writer

#################################################
# oci config and "login" check
######################################################
config, signer = create_signer(cmd.config_profile, cmd.is_instance_principals, cmd.is_delegation_token)
tenant_id = config['tenancy']

compartments= Login(config, signer, tenant_id)

print(f"Current configured region is: {config['region']}")
print("Do you want to get overview against this region only, or all subscribed regions?")
print("Press <Enter> to run against this region only, or type 'all' to run against all subscribed regions.")
user_input = input("Your choice [<Enter>/all]: ").strip().lower()

if user_input.lower() != "all":
    selected_regions = [config["region"]]
    print(f"Proceeding with just this region: {config['region']}")
else:
    # Get all subscribed regions for the tenancy
    identity_client = oci.identity.IdentityClient(config, signer=signer)
    selected_regions = SubscribedRegions(config, signer)
    print("Proceeding with all subscribed regions:")

esxi_hosts = []
esxi_donor_hosts = []
for region in selected_regions:
    config["region"] = region

    ocvp = oci.ocvp.EsxiHostClient(config, signer=signer)
    skip_region = False
    for c in compartments:
        print("Scanning " + region + ": compartments for unused billing terms (billing donors): " + c.fullpath + "                 ", end="\r")
        try:
            for host in oci.pagination.list_call_get_all_results(
                    ocvp.list_esxi_hosts,
                    compartment_id=c.details.id,
                    is_billing_donors_only=True,
                ).data:
                print("billing donor found: " + host.display_name)
                esxi_donor_hosts.append(host)
        except Exception as e:
            # Check if it's an OCI ServiceError and status is 404
            if hasattr(e, "status") and e.status == 404:
                # print(f"Region {config['region']} returned 404 (Not Found). Skipping region.")
                skip_region = True
                break
            else:
                print(f"Error retrieving ESXi hosts for region {config['region']}: {e}")
    if not skip_region:
        search_client = oci.resource_search.ResourceSearchClient(config, signer=signer)
        structured_search_details = oci.resource_search.models.StructuredSearchDetails(
            query="query vmwareesxihost resources",
            type="Structured"
        )

        print("Searching for all ESXi hosts using structured query...                                   ", end="\r")
        try:
            search_result = search_client.search_resources(structured_search_details)
            esxi_hosts_search = search_result.data.items
            for host in esxi_hosts_search:
                try:
                    # identifier is assumed to be the ESXi host OCID
                    detailed_host = ocvp.get_esxi_host(host.identifier).data
                    esxi_hosts.append(detailed_host)
                except Exception as detail_e:
                    print(f"Error retrieving details for ESXi Host {host.identifier}: {detail_e}")


        except Exception as e:
            print(f"Error during structured search for ESXi hosts: {e}")

###################################
# print results
###################################

TABLE_HEADERS = [
    "Region",
    "Compartment",
    "ESXi Host",
    "SDDC",
    "Lifecycle State",
    "Host Shape",
    "OCPU Count",
    "time-created",
    "Days old",
    "Current Commitment",
    "Contract End Date",
    "Next Commitment",
    "Days left",
]
rows = []
get_sddc = GetSDDCByOCID(config, signer)

for host in esxi_hosts:
    # Region from the host's OCID (source of truth), not from config or host.region
    region = region_from_ocid(getattr(host, "id", "") or getattr(host, "identifier", "")) or config.get("region", "")
    # Attempt to get each field, fallback to empty string/None if missing
    display_name = getattr(host, "display_name", "") or getattr(host, "name", "")
    compartment_id = GetCompartmentFullPath(compartments, getattr(host, "compartment_id", ""))
    sddc_ocid = getattr(host, "sddc_id", "")
    sddc = get_sddc(sddc_ocid) if sddc_ocid else None
    sddc_name = (getattr(sddc, "display_name", "") or "") if sddc else ""
    lifecycle_state = getattr(host, "lifecycle_state", "")
    host_shape_name = getattr(host, "current_sku", None)
    if host_shape_name and hasattr(host_shape_name, "name"):
        host_shape = host_shape_name.name
    else:
        host_shape = getattr(host, "host_shape_name", "")  # fallback

    host_ocpu_count = getattr(host, "host_ocpu_count", "")

    # Time created and days old
    time_created = getattr(host, "time_created", None)
    days_old = ""
    if time_created:
        if hasattr(time_created, "date"):
            time_created_date = time_created.date()
        elif hasattr(time_created, "strftime"):
            time_created_date = time_created
        else:
            time_created_date = None
        if time_created_date:
            days_old = (date.today() - time_created_date).days
            time_created = time_created_date.strftime("%Y-%m-%d")
        else:
            time_created = ""
    else:
        time_created = ""

    # Billing fields (these sometimes may be under different attribute names depending on API version)
    current_commitment = getattr(host, "current_commitment", "")
    contract_end_date = getattr(host, "billing_contract_end_date", "")
    next_commitment = getattr(host, "next_commitment", "")

    # If the above billing fields are in an object, try to extract
    if hasattr(host, "billing_term_info"):
        b = host.billing_term_info
        current_commitment = getattr(b, "current_commitment", current_commitment)
        contract_end_date = getattr(b, "billing_contract_end_date", contract_end_date)
        next_commitment = getattr(b, "next_commitment", next_commitment)

    # Days left = days until contract end (from billing_contract_end_date)
    days_left = ""
    if hasattr(contract_end_date, "strftime"):
        end_date = contract_end_date.date() if hasattr(contract_end_date, "date") else contract_end_date
        days_left = (end_date - date.today()).days
    # Format dates for display
    if hasattr(contract_end_date, "strftime"):
        contract_end_date = contract_end_date.strftime("%Y-%m-%d")
    if hasattr(next_commitment, "strftime"):
        next_commitment = (next_commitment.date() if hasattr(next_commitment, "date") else next_commitment).strftime("%Y-%m-%d")

    rows.append([
        region,
        compartment_id,
        display_name,
        sddc_name,
        lifecycle_state,
        host_shape,
        host_ocpu_count,
        time_created,
        days_old,
        current_commitment,
        contract_end_date,
        next_commitment,
        days_left,
    ])

print("\nESXi Host Billing Table:\n")
print_table(TABLE_HEADERS, rows, table_name="esxi_host_billing")

if not esxi_donor_hosts:
    print("No donor hosts found")
else:
    print("\nDonor Host Details:\n")
    donor_headers = ["Region", "Compartment", "Hostname", "Host Shape", "OCPU Count", "Current Commitment", "Contract End Date", "Days Left"]
    donor_rows = []
    for host in esxi_donor_hosts:
        region = getattr(host, "region", "")
        compartment_id = GetCompartmentFullPath(compartments, getattr(host, "compartment_id", ""))
        hostname = getattr(host, "display_name", "")
        host_shape = getattr(host, "host_shape_name", "")
        host_ocpu_count = getattr(host, "host_ocpu_count", "")
        current_commitment = getattr(host, "current_commitment", "")
        contract_end_date = getattr(host, "billing_contract_end_date", "")
        days_left = ""
        # Extract from billing_term_info if present
        if hasattr(host, "billing_term_info"):
            b = host.billing_term_info
            current_commitment = getattr(b, "current_commitment", current_commitment)
            contract_end_date = getattr(b, "billing_contract_end_date", contract_end_date)
        # Calculate days_left
        if hasattr(contract_end_date, "strftime"):
            end_date = contract_end_date.date() if hasattr(contract_end_date, "date") else contract_end_date
            days_left = (end_date - date.today()).days
            contract_end_date_str = contract_end_date.strftime("%Y-%m-%d")
        else:
            contract_end_date_str = contract_end_date
        donor_rows.append([
            region,
            compartment_id,
            hostname,
            host_shape,
            host_ocpu_count,
            current_commitment,
            contract_end_date_str,
            days_left,
        ])
    print_table(donor_headers, donor_rows, table_name="esxi_donor_hosts")