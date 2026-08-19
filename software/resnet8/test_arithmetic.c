/* Host-only edge test for gemmlowp's signed high multiply. */
#ifdef RESNET8_ARITHMETIC_TEST_MAIN
#include <stdio.h>
#include "resnet8.h"
int main(void) {
  int rc = resnet8_arithmetic_self_test();
  printf("arithmetic_edge_test=%s\n", rc == 0 ? "pass" : "fail");
  return rc != 0;
}
#endif
