#!/bin/sh
# Check if the current arbitrary UID is missing from the system
if ! whoami &> /dev/null; then
  USERNAME=${USER:-"vllm"}
  # Copy the base passwd file to your writable emptyDir
  cp /etc/passwd /tmp/passwd
  # Append the arbitrary running user to the writable file
  sed -i "s/^${USERNAME}:x:[0-9]\+:[0-9]\+:/${USERNAME}:x:$(id -u):0:/" /tmp/passwd
  # Force the container's glibc to look at your custom passwd file
  export LD_PRELOAD=libnss_wrapper.so
  export NSS_WRAPPER_PASSWD=/tmp/passwd
fi
exec "$@"
