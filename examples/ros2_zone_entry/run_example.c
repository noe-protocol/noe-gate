/*
 * examples/ros2_zone_entry/run_example.c
 *
 * Zone entry safety gate demo using the noe_core C FFI.
 *
 * Scenario
 * --------
 * A mobile robot requests entry to a restricted zone. Before entering, a
 * Noe safety gate evaluates a positive clearance predicate: the zone must be
 * known to be clear before the action is emitted.
 *
 * Chain:  shi @zone_clear khi sek mek @enter_zone_alpha sek nek
 *
 * Reading: "IF zone is known clear, THEN DO enter_zone_alpha."
 *
 * Policy (extracted by the adapter from the emitted domain):
 *   - domain: action    → zone is clear    → PERMITTED (action emitted)
 *   - domain: undefined → zone not clear   → BLOCKED (guard did not hold)
 *
 * Context fixtures
 * ----------------
 *   context_zone_clear.json    — @zone_clear=true  → PERMITTED
 *   context_zone_blocked.json  — @zone_clear=false → BLOCKED
 *
 * Usage
 * -----
 *   ./run_example <context_json_file>
 *
 * Example:
 *   ./run_example context_zone_blocked.json  # → BLOCKED
 *   ./run_example context_zone_clear.json    # → PERMITTED
 *
 * Build (from repo root after cargo build)
 * -----------------------------------------
 *   cd rust/noe_core && cargo build
 *   cc examples/ros2_zone_entry/run_example.c \
 *       -Irust/noe_core/include \
 *       -Lrust/noe_core/target/debug -lnoe_core \
 *       -Wl,-rpath,rust/noe_core/target/debug \
 *       -o examples/ros2_zone_entry/run_example
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "noe_core.h"

/* Read entire file contents into a malloc'd buffer. Caller frees. */
static char *read_file(const char *path) {
  FILE *f = fopen(path, "rb");
  if (!f) {
    perror(path);
    return NULL;
  }
  fseek(f, 0, SEEK_END);
  long len = ftell(f);
  rewind(f);
  char *buf = (char *)malloc((size_t)len + 1);
  if (!buf) {
    fclose(f);
    return NULL;
  }
  fread(buf, 1, (size_t)len, f);
  buf[len] = '\0';
  fclose(f);
  return buf;
}

int main(int argc, char *argv[]) {
  if (argc < 2) {
    fprintf(stderr,
            "Usage: %s <context_json_file>\n"
            "Example: %s context_zone_blocked.json\n",
            argv[0], argv[0]);
    return 1;
  }

  /* Load context JSON from file */
  char *context_json = read_file(argv[1]);
  if (!context_json) {
    fprintf(stderr, "Failed to read context file: %s\n", argv[1]);
    return 1;
  }

  /* ---------------------------------------------------------------
   * Evaluate the safety chain via noe_core FFI
   * --------------------------------------------------------------- */
  const char *chain = "shi @zone_clear khi sek mek @enter_zone_alpha sek nek";
  printf("noe_core version : %s\n", noe_version());
  printf("chain            : %s\n", chain);
  printf("context file     : %s\n", argv[1]);
  printf("\n");

  char *result = noe_eval_json(chain, context_json, "strict");
  free(context_json);

  if (!result) {
    fprintf(stderr, "noe_eval_json returned NULL (catastrophic OOM)\n");
    return 1;
  }

  printf("raw result       : %s\n\n", result);

  /* ---------------------------------------------------------------
   * Extract domain from the result JSON.
   * We use simple substring checks — sufficient for this demo.
   * A production ROS2 adapter would parse with a proper JSON library.
   * --------------------------------------------------------------- */
  int is_action_domain = (strstr(result, "\"domain\":\"action\"") != NULL) ||
                         (strstr(result, "\"domain\":\"list\"") != NULL);
  int is_error_domain = (strstr(result, "\"domain\":\"error\"") != NULL);
  int is_undefined = (strstr(result, "\"domain\":\"undefined\"") != NULL);

  noe_free_string(result);

  /* ---------------------------------------------------------------
   * Policy decision (made by the adapter, not by Noe)
   * --------------------------------------------------------------- */
  if (is_error_domain) {
    printf("DECISION: ERROR — Noe returned an error envelope; denying entry by "
           "default.\n");
    return 2;
  }

  if (is_action_domain) {
    printf("DECISION: PERMITTED — zone is known clear. Robot may enter.\n");
    return 0;
  }

  if (is_undefined) {
    printf("DECISION: BLOCKED — zone clearance not established.\n");
    printf("          Robot must not enter. Halt command required.\n");
    return 0;
  }

  printf("DECISION: ERROR — unhandled domain.\n");
  return 2;
}
