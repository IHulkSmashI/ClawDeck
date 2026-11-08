// SPDX-License-Identifier: GPL-2.0-only
// Copyright (C) 2026 Kisenir (r/MSIClaw). Used in ClawDeck with permission (2026-11-08).
/*
 * MSI Claw 8 EX AI+ (Intel Panther Lake ISH sensor hub, 8087:0AC2):
 * the IMU is declared as two *custom* HID sensors (usage 0x2000e1), which no
 * stock Linux driver binds.  The data layout is a perfectly standard
 * 3-axis int32 report (values in 1e-6 g and 1e-6 deg/s, unit exponent -6), so
 * relabel them as a standard Accelerometer 3D and Gyrometer 3D:
 *
 *   report id 1: collection 0x2000e1 -> 0x200073 (Accelerometer 3D)
 *                custom values 0x200544..46 -> accel X/Y/Z 0x200453..55
 *   report id 2: collection 0x2000e1 -> 0x200076 (Gyrometer 3D)
 *                custom values 0x200544..46 -> angular velocity X/Y/Z 0x200457..59
 *
 * Gyro bias correction: every gyro input report (id 2) has a per-axis offset
 * subtracted from its three int32 values (offsets live in the global `cfg`, which a
 * userspace helper keeps up to date from the measured resting bias).
 *
 * Then hid-sensor-hub + hid-sensor-accel-3d / hid-sensor-gyro-3d create
 * accel_3d / gyro_3d IIO devices.  Length-preserving descriptor patch only.
 */
#include "vmlinux.h"
#include "hid_bpf.h"
#include "hid_bpf_helpers.h"
#include <bpf/bpf_tracing.h>

#define VID_INTEL 0x8087
#define PID_ISH_SENSOR_HUB 0x0AC2
#define EXPECTED_RDESC_SIZE 2247

HID_BPF_CONFIG(
	HID_DEVICE(BUS_INTEL_ISHTP, HID_GROUP_ANY, VID_INTEL, PID_ISH_SENSOR_HUB)
);

/* Offsets of the bytes to change in the 2247-byte descriptor (verified offline). */
#define ACCEL_COLL   11   /* 09 e1 -> 09 73 */
#define GYRO_COLL    487  /* 09 e1 -> 09 76 */
/* "0a 44 05" (Usage 0x0544) starts at these offsets */
#define ACCEL_X 401
#define ACCEL_Y 422
#define ACCEL_Z 443
#define GYRO_X  877
#define GYRO_Y  898
#define GYRO_Z  919

static __always_inline int is_expected(__u8 *d)
{
	return d[ACCEL_COLL - 1] == 0x09 && d[ACCEL_COLL] == 0xe1 &&
	       d[GYRO_COLL - 1] == 0x09 && d[GYRO_COLL] == 0xe1 &&
	       d[ACCEL_X] == 0x0a && d[ACCEL_X + 1] == 0x44 && d[ACCEL_X + 2] == 0x05 &&
	       d[ACCEL_Y] == 0x0a && d[ACCEL_Y + 1] == 0x45 && d[ACCEL_Y + 2] == 0x05 &&
	       d[ACCEL_Z] == 0x0a && d[ACCEL_Z + 1] == 0x46 && d[ACCEL_Z + 2] == 0x05 &&
	       d[GYRO_X] == 0x0a && d[GYRO_X + 1] == 0x44 && d[GYRO_X + 2] == 0x05 &&
	       d[GYRO_Y] == 0x0a && d[GYRO_Y + 1] == 0x45 && d[GYRO_Y + 2] == 0x05 &&
	       d[GYRO_Z] == 0x0a && d[GYRO_Z + 1] == 0x46 && d[GYRO_Z + 2] == 0x05;
}

SEC(HID_BPF_RDESC_FIXUP)
int BPF_PROG(ish_imu_fix_rdesc, struct hid_bpf_ctx *hctx)
{
	__u8 *data = hid_bpf_get_data(hctx, 0 /* offset */, 4096 /* size */);

	if (!data)
		return 0; /* EPERM check */

	if (hctx->size != EXPECTED_RDESC_SIZE || !is_expected(data))
		return 0; /* unknown firmware layout: leave untouched */

	data[ACCEL_COLL] = 0x73;                         /* Accelerometer 3D */
	data[ACCEL_X + 1] = 0x53; data[ACCEL_X + 2] = 0x04;  /* 0x0453 */
	data[ACCEL_Y + 1] = 0x54; data[ACCEL_Y + 2] = 0x04;  /* 0x0454 */
	data[ACCEL_Z + 1] = 0x55; data[ACCEL_Z + 2] = 0x04;  /* 0x0455 */

	data[GYRO_COLL] = 0x76;                          /* Gyrometer 3D */
	data[GYRO_X + 1] = 0x57; data[GYRO_X + 2] = 0x04;    /* 0x0457 */
	data[GYRO_Y + 1] = 0x58; data[GYRO_Y + 2] = 0x04;    /* 0x0458 */
	data[GYRO_Z + 1] = 0x59; data[GYRO_Z + 2] = 0x04;    /* 0x0459 */

	return 0;
}


/* Runtime configuration, updated from userspace (identified there by .magic).
 * off[] is in raw gyro units (1e-5 deg/s) and is SUBTRACTED from each axis. */
struct imu_cfg {
	__u32 magic;
	__s32 off[3];
	__u32 enable;
};
struct imu_cfg cfg = { .magic = 0x474F4646 /* "GOFF" */, .off = { 0, 0, 0 }, .enable = 1 };

/* Gyro input report id 2: [id][..40 bytes..]; the three axes are int32 LE at bytes 27, 31, 35 */
#define GYRO_REPORT_ID 2
#define GYRO_REPORT_SIZE 41
#define GYRO_AXIS0 27

static __always_inline __s32 rd32(const __u8 *p)
{
	return (__s32)((__u32)p[0] | ((__u32)p[1] << 8) | ((__u32)p[2] << 16) | ((__u32)p[3] << 24));
}

static __always_inline void wr32(__u8 *p, __s32 v)
{
	p[0] = v & 0xff; p[1] = (v >> 8) & 0xff; p[2] = (v >> 16) & 0xff; p[3] = (v >> 24) & 0xff;
}

SEC(HID_BPF_DEVICE_EVENT)
int BPF_PROG(ish_imu_event, struct hid_bpf_ctx *hctx)
{
	__u8 *d = hid_bpf_get_data(hctx, 0 /* offset */, GYRO_REPORT_SIZE);

	if (!d)
		return 0; /* EPERM check */
	if (hctx->size < GYRO_REPORT_SIZE || d[0] != GYRO_REPORT_ID || !cfg.enable)
		return 0;

	wr32(d + GYRO_AXIS0,      rd32(d + GYRO_AXIS0)      - cfg.off[0]);
	wr32(d + GYRO_AXIS0 + 4,  rd32(d + GYRO_AXIS0 + 4)  - cfg.off[1]);
	wr32(d + GYRO_AXIS0 + 8,  rd32(d + GYRO_AXIS0 + 8)  - cfg.off[2]);
	return 0;
}

HID_BPF_OPS(msi_claw_ish_imu) = {
	.hid_device_event = (void *)ish_imu_event,
	.hid_rdesc_fixup = (void *)ish_imu_fix_rdesc,
};

SEC("syscall")
int probe(struct hid_bpf_probe_args *ctx)
{
	/* Only attach to the known descriptor layout. */
	if (ctx->rdesc_size != EXPECTED_RDESC_SIZE || !is_expected(ctx->rdesc))
		ctx->retval = -EINVAL;
	else
		ctx->retval = 0;
	return 0;
}

char _license[] SEC("license") = "GPL";
