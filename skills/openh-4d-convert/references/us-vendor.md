# Vendor 4D ultrasound

## The problem

Philips and GE store 4D echo volumes in **private DICOM tag blocks**. `dcm2niix`
does not decode them. Handing it such a file does not fail — it produces a NIfTI
that opens cleanly, has a plausible affine, and does not represent the
acquisition.

That is worse than a failure, so OpenH-4D intercepts these before `dcm2niix` is
invoked.

## Detection

`openh4d.us_vendor.detect()` looks for `Modality == "US"` together with a known
private creator string:

| Vendor | Private creator contains |
|---|---|
| Philips | `philips us imaging dd`, `philips imaging dd`, `philips3d` |
| GE Kretz / Voluson | `kretz`, `ge kretz us`, `ge_kretz` |

Matched case-insensitively as a substring, because vendors pad and version these
strings. A CT with a Philips private block is *not* claimed — the check is for 4D
echo, not for anything a vendor ever wrote.

`convert_dicom` runs this before anything else, so you do not have to.

## Current status

**The detection, dispatch, self-check and fallback are complete and tested. The
two vendor decoder bodies are not.**

Neither OpenH-4D example dataset contains vendor 4D echo, so there are no real
bytes to build a decoder against. A decoder written from documentation alone
would produce volumes that look right and are not — which is precisely the
failure this module exists to prevent. So each decoder raises
`DecoderNotImplemented`, and the fallback turns that into an actionable
instruction.

**Finishing them needs one real Philips 4D study and one real GE Voluson 4D
study.** If you have access to either and can share it, that unblocks this
directly — raise it with the OpenH-4D organizers at `openh.data+4d@gmail.com`.

## What a decoder must satisfy

Every decoder's output passes through `self_check()` before it is written, and a
decoder that fails it **declines** rather than emitting the volume:

| Check | Why |
|---|---|
| 4D array, at least 2 time points | it is a 4D initiative |
| voxel size 0.05–5 mm | outside that range the geometry was misread — metres read as millimetres is the classic version |
| non-degenerate affine | voxels must map to physical space |
| all values finite | |
| not constant | all-same voxels means nothing was decoded, however well-formed the array is |

This gate is the point. "We produced bytes" and "we decoded it" are different
claims, and only the second one is worth writing to disk.

## The fallback

When no decoder matches, or one declines, the contributor gets:

> Export this study from your vendor workstation in a standard format and convert
> that instead:
>   - Cartesian ("DICOM 3D", "Cartesian volume") DICOM, or
>   - NRRD / MHD / NIfTI, one file per time point or one 4D sequence file
>
> Then run: `python -m openh4d.convert_nrrd <file> --out <study-dir>`
>
> A vendor-proprietary 4D echo file converted by a general DICOM tool produces a
> volume with the wrong geometry, which is worse than no volume at all.

**This is a good outcome, not a workaround.** A Cartesian export from the vendor's
own software is produced by the people who know the format, with the scan
conversion the vendor intends. It is more trustworthy than anything a third-party
decoder would reconstruct.

So when a contributor hits this, the advice is not "wait for the decoder" — it is
"export from your workstation", which works today and works better.

### How to ask for the export

Vendor software calls this different things. Useful phrasings:

- **Philips QLAB / EchoPAC**: export as "Cartesian DICOM" or "DICOM 3D"; some
  versions call it "Export volume".
- **GE Voluson / 4D View**: export as Cartesian volume or Cartesian DICOM rather
  than the native `.vol`.
- Failing that, any research export to NRRD, MHD or NIfTI, one file per time
  point, works — just keep the files in acquisition order and zero-pad the names.

Ask them to confirm the export preserved the voxel spacing, because some viewer
exports write isotropic-by-assumption geometry that does not match the
acquisition.

## Recording what happened

When a decoder does run, `study.json.us.vendor_decoder` records its name and
version, so a downstream user can tell which volumes went through a
reverse-engineered path and which came from a vendor export. That distinction
matters for anyone modelling geometry-sensitive quantities.

Also fill in, from the contributor:

| Field | Values |
|---|---|
| `us.source` | `4d_tee`, `4d_percutaneous`, `other` — the RFP asks for this |
| `us.probe` | probe model |
| `us.frame_rate_hz` | volume rate, which for 4D echo is the limiting factor on temporal resolution |

## Burned-in annotation

Ultrasound is the modality where burned-in patient information is most common —
name and date rendered into the pixels by the scanner's display pipeline.
Removing DICOM tags does nothing about it.

`BurnedInAnnotation` (0028,0301) should say `YES` when present, but it is
frequently wrong or absent. Look at the corners of an actual frame. If text is
there, it must be cropped or redacted before submission; see
[`../../openh-4d-shared/phi-checklist.md`](../../openh-4d-shared/phi-checklist.md).
