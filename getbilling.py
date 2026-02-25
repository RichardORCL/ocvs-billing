import sys
import time
import oci
import requests

from ocimodules.functions import login, input_command_line, create_signer, check_oci_version, MyWriter

# Disable OCI CircuitBreaker feature
oci.circuit_breaker.NoCircuitBreakerStrategy()

#################################################
#           Application Configuration           #
#################################################
min_version_required = "2.167.0"
application_version = "25.02.2026"

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

login(config, signer)

