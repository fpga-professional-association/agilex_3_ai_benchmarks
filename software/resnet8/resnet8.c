#include "resnet8.h"

#include <limits.h>

#include "generated_model.h"

static int32_t r8_sat32(int64_t x) {
  if (x > INT32_MAX) return INT32_MAX;
  if (x < INT32_MIN) return INT32_MIN;
  return (int32_t)x;
}

static int8_t r8_sat8(int32_t x) {
  if (x > 127) return 127;
  if (x < -128) return -128;
  return (int8_t)x;
}

/* TFLite gemmlowp SaturatingRoundingDoublingHighMul. */
static int32_t r8_high_mul(int32_t a, int32_t b) {
  int64_t p = (int64_t)a * (int64_t)b;
  int64_t nudge;
  /* gemmlowp uses a signed 2^30 nudge and division by 2^31.  The one
     overflowing product is explicitly saturated by the reference kernel. */
  if (a == INT32_MIN && b == INT32_MIN) return INT32_MAX;
  nudge = (p >= 0) ? ((int64_t)1 << 30) : (1 - ((int64_t)1 << 30));
  return r8_sat32((p + nudge) / ((int64_t)1 << 31));
}

/* TFLite MultiplyByQuantizedMultiplier, with a signed shift. */
static int32_t r8_mul_q(int32_t x, int32_t multiplier, int shift) {
  if (shift >= 0) {
    int64_t wide = (int64_t)x * ((int64_t)1 << shift);
    return r8_high_mul(r8_sat32(wide), multiplier);
  }
  {
    int exponent = -shift;
    int32_t y = r8_high_mul(x, multiplier);
    if (exponent <= 0) return y;
    if (exponent >= 31) return y < 0 ? -1 : 0;
    {
      int32_t mask = ((int32_t)1 << exponent) - 1;
      int32_t remainder = y & mask;
      int32_t threshold = (mask >> 1) + (y < 0);
      return (y >> exponent) + (remainder > threshold);
    }
  }
}

static void r8_conv_run(const int8_t *src, int8_t *dst,
                        const r8_conv_desc *d) {
  int pad_h = d->output_h * d->stride_h - d->input_h + d->kernel_h - d->stride_h;
  int pad_w = d->output_w * d->stride_w - d->input_w + d->kernel_w - d->stride_w;
  if (pad_h < 0) pad_h = 0;
  if (pad_w < 0) pad_w = 0;
  int pad_top = pad_h / 2;
  int pad_left = pad_w / 2;
  int oh, ow, oc, ky, kx, ic;
  for (oh = 0; oh < d->output_h; ++oh) {
    for (ow = 0; ow < d->output_w; ++ow) {
      for (oc = 0; oc < d->output_c; ++oc) {
        int32_t acc = d->bias[oc];
        for (ky = 0; ky < d->kernel_h; ++ky) {
          int iy = oh * d->stride_h + ky - pad_top;
          for (kx = 0; kx < d->kernel_w; ++kx) {
            int ix = ow * d->stride_w + kx - pad_left;
            const int8_t *in = 0;
            if (iy >= 0 && iy < d->input_h && ix >= 0 && ix < d->input_w)
              in = src + (iy * d->input_w + ix) * d->input_c;
            for (ic = 0; ic < d->input_c; ++ic) {
              int32_t a = in ? ((int32_t)in[ic] - d->input_zero_point) : 0;
              int32_t w = d->weights[((oc * d->kernel_h + ky) * d->kernel_w + kx) * d->input_c + ic];
              acc += a * w;
            }
          }
        }
        {
          int32_t y = r8_mul_q(acc, d->multiplier[oc], d->shift[oc]) + d->output_zero_point;
          if (d->fused_relu && y < d->output_zero_point) y = d->output_zero_point;
          dst[(oh * d->output_w + ow) * d->output_c + oc] = r8_sat8(y);
        }
      }
    }
  }
}

static void r8_add_run(const int8_t *a, const int8_t *b, int8_t *out,
                       const r8_add_desc *d) {
  int i;
  for (i = 0; i < d->count; ++i) {
    int32_t y = r8_mul_q((int32_t)a[i] - d->input1_zero_point, d->m1[0], d->s1[0]);
    y += r8_mul_q((int32_t)b[i] - d->input2_zero_point, d->m2[0], d->s2[0]);
    y += d->output_zero_point;
    if (d->fused_relu && y < d->output_zero_point) y = d->output_zero_point;
    out[i] = r8_sat8(y);
  }
}

int resnet8_arithmetic_self_test(void) {
  /* Edge cases from gemmlowp's SaturatingRoundingDoublingHighMul contract,
     including the only overflowing INT32 product and negative ties. */
  if (r8_high_mul(INT32_MIN, INT32_MIN) != INT32_MAX) return -1;
  if (r8_high_mul(INT32_MAX, INT32_MAX) != INT32_MAX - 1) return -1;
  if (r8_high_mul(INT32_MIN, INT32_MAX) != INT32_MIN + 1) return -1;
  if (r8_high_mul(1, INT32_MIN) != -1) return -1;
  if (r8_high_mul(-1, INT32_MIN) != 1) return -1;
  if (r8_high_mul(1073741824, 1073741824) != 536870912) return -1;
  return 0;
}

static void r8_average_pool(const int8_t *src, int8_t *dst) {
  int c;
  for (c = 0; c < 64; ++c) {
    int y, x;
    int32_t sum = 0;
    for (y = 0; y < 8; ++y)
      for (x = 0; x < 8; ++x)
        sum += (int32_t)src[(y * 8 + x) * 64 + c] + 128;
    /* input/output scales are equal in this FlatBuffer, so the exact
       TFLite multiplier is 1/64 (m=2^30, shift=-6). */
    dst[c] = r8_sat8(r8_mul_q(sum, 1073741824, -6) - 128);
  }
}

static void r8_fc_run(const int8_t *src, int8_t *dst) {
  int row, col;
  for (row = 0; row < r8_fc.rows; ++row) {
    int32_t acc = r8_fc.bias[row];
    for (col = 0; col < r8_fc.cols; ++col)
      acc += ((int32_t)src[col] - r8_fc.input_zero_point) *
             (int32_t)r8_fc.weights[row * r8_fc.cols + col];
    dst[row] = r8_sat8(r8_mul_q(acc, r8_fc.multiplier, r8_fc.shift) + r8_fc.output_zero_point);
  }
}

static void r8_softmax(const int8_t *logits, int8_t *out) {
  int i;
  int8_t maxv = logits[0];
  uint32_t sum = 0;
  uint16_t e[RESNET8_OUTPUT_CLASSES];
  for (i = 1; i < RESNET8_OUTPUT_CLASSES; ++i)
    if (logits[i] > maxv) maxv = logits[i];
  for (i = 0; i < RESNET8_OUTPUT_CLASSES; ++i) {
    int delta = (int)maxv - (int)logits[i];
    e[i] = (delta < 256) ? r8_softmax_exp_q15[delta] : 0;
    sum += e[i];
  }
  if (sum == 0) sum = 1;
  for (i = 0; i < RESNET8_OUTPUT_CLASSES; ++i) {
    uint32_t p = ((uint32_t)e[i] * 256u + sum / 2u) / sum;
    if (p > 255u) p = 255u;
    out[i] = (int8_t)((int)p - 128);
  }
}

static void r8_infer_layers(const int8_t input[RESNET8_INPUT_BYTES],
                            int8_t output[RESNET8_OUTPUT_CLASSES],
                            uint32_t *checksums) {
  /* Three fixed 16,384-byte slots cover the largest 32x32x16 activation and
     allow both residual branches to remain live.  Smaller 32/64 channel
     tensors use only the prefix of a slot. */
  static int8_t arena[R8_ACTIVATION_ARENA_BYTES];
  int8_t *s0 = arena;
  int8_t *s1 = arena + R8_ACTIVATION_SLOT_BYTES;
  int8_t *s2 = arena + 2 * R8_ACTIVATION_SLOT_BYTES;
  r8_conv_run(input, s0, &r8_conv[0]);       /* tensor 22 */
  if (checksums) checksums[0] = resnet8_checksum(s0, 16384);
  r8_conv_run(s0, s1, &r8_conv[1]);           /* tensor 23 */
  if (checksums) checksums[1] = resnet8_checksum(s1, 16384);
  r8_conv_run(s1, s2, &r8_conv[2]);           /* tensor 24 */
  if (checksums) checksums[2] = resnet8_checksum(s2, 16384);
  r8_add_run(s0, s2, s0, &r8_add[0]);         /* tensor 25 */
  if (checksums) checksums[3] = resnet8_checksum(s0, 16384);
  r8_conv_run(s0, s1, &r8_conv[3]);           /* tensor 26 */
  if (checksums) checksums[4] = resnet8_checksum(s1, 8192);
  r8_conv_run(s1, s2, &r8_conv[4]);           /* tensor 27 */
  if (checksums) checksums[5] = resnet8_checksum(s2, 8192);
  r8_conv_run(s0, s1, &r8_conv[5]);           /* tensor 28; slot 1 is now free */
  if (checksums) checksums[6] = resnet8_checksum(s1, 8192);
  r8_add_run(s1, s2, s1, &r8_add[1]);         /* tensor 29 */
  if (checksums) checksums[7] = resnet8_checksum(s1, 8192);
  r8_conv_run(s1, s2, &r8_conv[6]);           /* tensor 30 */
  if (checksums) checksums[8] = resnet8_checksum(s2, 4096);
  r8_conv_run(s2, s0, &r8_conv[7]);           /* tensor 31 */
  if (checksums) checksums[9] = resnet8_checksum(s0, 4096);
  r8_conv_run(s1, s2, &r8_conv[8]);           /* tensor 32 */
  if (checksums) checksums[10] = resnet8_checksum(s2, 4096);
  /* ADD input order matters because each branch has its own multiplier. */
  r8_add_run(s2, s0, s0, &r8_add[2]);         /* tensor 33 */
  if (checksums) checksums[11] = resnet8_checksum(s0, 4096);
  r8_average_pool(s0, s1);                    /* tensor 34 */
  if (checksums) checksums[12] = resnet8_checksum(s1, 64);
  if (checksums) checksums[13] = checksums[12]; /* tensor 35 is a no-op reshape */
  r8_fc_run(s1, s2);                          /* tensor 36 */
  if (checksums) checksums[14] = resnet8_checksum(s2, 10);
  r8_softmax(s2, output);                     /* tensor 37 */
  if (checksums) checksums[15] = resnet8_checksum(output, 10);
}

void resnet8_infer(const int8_t input[RESNET8_INPUT_BYTES],
                   int8_t output[RESNET8_OUTPUT_CLASSES]) {
  r8_infer_layers(input, output, 0);
}

void resnet8_infer_layer_checksums(const int8_t input[RESNET8_INPUT_BYTES],
                                   uint32_t checksums[16],
                                   int8_t output[RESNET8_OUTPUT_CLASSES]) {
  r8_infer_layers(input, output, checksums);
}

void resnet8_make_synthetic_input(int8_t input[RESNET8_INPUT_BYTES]) {
  uint32_t x = 0x13579bdfu;
  int i;
  for (i = 0; i < RESNET8_INPUT_BYTES; ++i) {
    x = x * 1664525u + 1013904223u;
    input[i] = (int8_t)((x >> 24) ^ (uint32_t)(i * 29));
  }
}

uint32_t resnet8_checksum(const int8_t *data, int count) {
  uint32_t h = 2166136261u;
  int i;
  for (i = 0; i < count; ++i) {
    h ^= (uint8_t)data[i];
    h *= 16777619u;
  }
  return h;
}

int resnet8_self_test(uint32_t *checksum, int *class_index) {
  int8_t input[RESNET8_INPUT_BYTES];
  int8_t output[RESNET8_OUTPUT_CLASSES];
  uint32_t layers[16];
  static const uint32_t expected[16] = {
    UINT32_C(0x70826a1b), UINT32_C(0x17fe510c), UINT32_C(0xc1e7bf33), UINT32_C(0x8c8976f5),
    UINT32_C(0x750876c7), UINT32_C(0x36ff6295), UINT32_C(0xbbeb0dcf), UINT32_C(0xb4133f8b),
    UINT32_C(0xcd6d1218), UINT32_C(0x79508a4f), UINT32_C(0x963f9c7a), UINT32_C(0x5bd06036),
    UINT32_C(0x60d67475), UINT32_C(0x60d67475), UINT32_C(0xa8daf8c1), UINT32_C(0x867c28f5)
  };
  int i;
  if (resnet8_arithmetic_self_test() != 0) return -2;
  resnet8_make_synthetic_input(input);
  r8_infer_layers(input, output, layers);
  if (checksum) *checksum = resnet8_checksum(output, RESNET8_OUTPUT_CLASSES);
  if (class_index) {
    *class_index = 0;
    for (i = 1; i < RESNET8_OUTPUT_CLASSES; ++i)
      if (output[i] > output[*class_index]) *class_index = i;
  }
  /* These are independently generated implementation regression values, not
     an accuracy/golden-output claim or a TFLite-runtime oracle. */
  for (i = 0; i < 16; ++i)
    if (layers[i] != expected[i]) return -1;
  return 0;
}
