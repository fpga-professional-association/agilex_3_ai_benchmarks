#ifndef RESNET8_H
#define RESNET8_H

#include <stdint.h>

#define RESNET8_INPUT_BYTES (32 * 32 * 3)
#define RESNET8_OUTPUT_CLASSES 10

/* Run the generated int8 graph. input/output are NHWC and int8. */
void resnet8_infer(const int8_t input[RESNET8_INPUT_BYTES],
                   int8_t output[RESNET8_OUTPUT_CLASSES]);
void resnet8_infer_layer_checksums(const int8_t input[RESNET8_INPUT_BYTES],
                                   uint32_t checksums[16],
                                   int8_t output[RESNET8_OUTPUT_CLASSES]);

/* Reproducible host/target-independent input and FNV-1a output checksum. */
void resnet8_make_synthetic_input(int8_t input[RESNET8_INPUT_BYTES]);
uint32_t resnet8_checksum(const int8_t *data, int count);
int resnet8_self_test(uint32_t *checksum, int *class_index);
int resnet8_arithmetic_self_test(void);

#endif
