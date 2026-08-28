# FMU Compatibility Matrix

This matrix records results reproduced on Windows win64 with Python 3.13 and
the pinned FMPy 0.3.31 dependency. `inspect` means the public
`backend.load_fmu()` path, not merely opening the ZIP container.

| FMU | Inspect | Validate | Run | Input | Output | CSV | Note |
|---|---:|---:|---:|---:|---:|---:|---|
| `Stair.fmu` | Yes | Yes | Yes | N/A | Yes | Yes | FMI 2 CS; `counter` changes from 1 to 2 over 0..1 s. |
| `VanDerPol.fmu` | Yes | Yes | Yes | N/A | Yes | Yes | FMI 2 CS; `mu` override and selected `x0` regression pass. |
| `VanDerPol-fmi3.fmu` | Yes | Yes | Yes | N/A | Yes | Yes | Basic FMI 3 CS; `mu` override and selected `x0` regression pass. |
| `bouncingBall.fmu` | No | No | No | N/A | N/A | No | Container metadata says FMI 1.0 Model Exchange. Farcel intentionally supports only FMI 2/3 metadata and only CS execution. `UNSUPPORTED_FMI` includes `fmi_version=1.0` and `parseable=true`. |
| `manipulator.fmu` | Yes | Yes | No | Setters work | Initial sample only | No | FMI 2 CS. First `doStep` returns error because the native FMU reports `Singular matrix not invertible (getrf).`; reproduced with zero/non-zero inputs, explicit defaults, and step sizes 0.1 through 0.0001. Farcel reports the native diagnostic and releases resources. |
| `LateralMotionControl.fmu` | Yes, with warnings | Yes | Yes | Initial + scheduled | Yes | Yes | FMI 2 CS. XML references undeclared unit `m/s`; this recoverable unit-definition problem is retained in metadata diagnostics. A generic boolean trigger schedule produces meaningful task execution. |

## Metadata inventory

| FMU | Inputs | Parameters | Outputs | Scalar types |
|---|---:|---:|---:|---|
| `Stair.fmu` | 0 | 0 | 1 | Integer |
| `VanDerPol.fmu` | 0 | 1 | 2 | Real |
| `VanDerPol-fmi3.fmu` | 0 | 1 | 2 | Float64 |
| `manipulator.fmu` | 2 | 10 | 2 | Real |
| `LateralMotionControl.fmu` | 19 | 34 | 40 | Real, Integer, Boolean |
| `bouncingBall.fmu` | FMI 1 metadata has no declared input causality | 2 parameter variables (`g`, `e`) | No declared output causality | Real |

## Root causes

### bouncingBall

This fixture is FMI 1.0 Model Exchange only. It has no Co-Simulation interface,
so running it would require both FMI 1 support and a Model Exchange solver,
which are outside the current milestone. No implementation change attempts to
reinterpret it as an FMI 2/3 Co-Simulation FMU.

### manipulator

Instantiation, setup, initialization, initial input writes, and the initial
output read succeed. The first native `fmi2DoStep` fails and logs:

```text
Singular matrix not invertible (getrf).
```

The failure is unchanged by setting `ref1`/`ref2`, using explicit parameter
start values, or reducing the step size. It is therefore not caused by Farcel
omitting initial or time-varying inputs. The integration test asserts the exact
FMU diagnostic and cleanup instead of treating a generic `STEP_ERROR` as an
expected success condition.

### LateralMotionControl

Strict schema validation reports two undeclared `m/s` unit references. Farcel
allows only this recoverable validation category to continue and preserves both
messages in `ModelMetadata.diagnostics`; all other schema validation failures
remain errors.

The FMU can advance without external inputs, but its task blocks remain inactive
when all trigger inputs stay false. Meaningful behavior therefore needs input
updates at communication points. The regression sets `velocity=20` initially,
pulses the generic sensor activation/finished inputs, and observes `sens_out_4`
change from 10 to 20 while time and step outputs advance.

## Input schedule semantics

`SimulationConfig.input_schedule` is an optional tuple of `InputUpdate` values.
Each update time must align with a communication point, updates must be strictly
increasing, and values are held until changed. Farcel applies the update for the
current communication point immediately before `doStep`. With an empty schedule,
the original execution path is unchanged.

## Communication and sampling semantics

`SimulationConfig.communication_step` controls every Co-Simulation `doStep`
communication point. `output_interval` controls only when Farcel records an
already-reached point in `SimulationResult`; it never changes input scheduling
or the FMU step size. If omitted, `output_interval` defaults to the communication
step for Phase 1 compatibility. An explicit output interval must be a positive,
finite integer multiple of the communication step, so no interpolation is
required. Farcel records the initial state, samples each matching communication
point, and records the final state on successful completion when it was not
otherwise sampled. Consequently, `completed_steps` and `sample_count` are
independent metrics from Phase 2.0A onward.

The high-level run remains synchronous and blocking. Stop, Cancel, and Progress
are not part of this milestone.

Arrays, FMI 3 Binary/Clock, Event Mode, Early Return, Intermediate Update,
Scheduled Execution, Model Exchange execution, and FMI 1 remain unsupported.
