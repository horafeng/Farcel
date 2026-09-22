/* Farcel Standard Block Library -- Sine FMI 2.0 Co-Simulation FMU. SPDX-License-Identifier: MIT */
#include <math.h>
#include <stddef.h>
#include <stdlib.h>

#ifdef _WIN32
#define FMI2_EXPORT __declspec(dllexport)
#else
#define FMI2_EXPORT
#endif

typedef void* fmi2Component;
typedef const char* fmi2String;
typedef unsigned int fmi2ValueReference;
typedef double fmi2Real;
typedef int fmi2Integer;
typedef int fmi2Boolean;
typedef int fmi2Type;
typedef int fmi2Status;
typedef int fmi2StatusKind;
typedef char fmi2Byte;
typedef void* fmi2FMUstate;
typedef struct fmi2CallbackFunctions fmi2CallbackFunctions;
enum { fmi2OK = 0, fmi2Error = 3, fmi2CoSimulation = 1 };
enum { VR_AMPLITUDE = 0, VR_FREQUENCY_HZ = 1, VR_PHASE_RAD = 2, VR_OFFSET = 3, VR_Y = 4 };

typedef struct {
    fmi2Real amplitude;
    fmi2Real frequency_hz;
    fmi2Real phase_rad;
    fmi2Real offset;
    fmi2Real time;
} SineInstance;

static fmi2Real sine_output(const SineInstance* instance) {
    const fmi2Real two_pi = 6.283185307179586476925286766559;
    return instance->offset + instance->amplitude * sin(two_pi * instance->frequency_hz * instance->time + instance->phase_rad);
}

FMI2_EXPORT const char* fmi2GetTypesPlatform(void) { return "default"; }
FMI2_EXPORT const char* fmi2GetVersion(void) { return "2.0"; }
FMI2_EXPORT fmi2Status fmi2SetDebugLogging(fmi2Component c, fmi2Boolean logging, size_t count, const fmi2String categories[]) { (void)c; (void)logging; (void)count; (void)categories; return fmi2OK; }
FMI2_EXPORT fmi2Component fmi2Instantiate(fmi2String name, fmi2Type type, fmi2String guid, fmi2String resource, const fmi2CallbackFunctions* callbacks, fmi2Boolean visible, fmi2Boolean logging) {
    SineInstance* instance;
    (void)name; (void)guid; (void)resource; (void)callbacks; (void)visible; (void)logging;
    if (type != fmi2CoSimulation) { return NULL; }
    instance = (SineInstance*)calloc(1, sizeof(SineInstance));
    if (instance != NULL) { instance->amplitude = 1.0; instance->frequency_hz = 1.0; }
    return instance;
}
FMI2_EXPORT void fmi2FreeInstance(fmi2Component c) { free(c); }
FMI2_EXPORT fmi2Status fmi2SetupExperiment(fmi2Component c, fmi2Boolean tolerance_defined, fmi2Real tolerance, fmi2Real start_time, fmi2Boolean stop_time_defined, fmi2Real stop_time) {
    SineInstance* instance = (SineInstance*)c;
    (void)tolerance_defined; (void)tolerance; (void)stop_time_defined; (void)stop_time;
    if (instance == NULL) { return fmi2Error; }
    instance->time = start_time;
    return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2EnterInitializationMode(fmi2Component c) { return c == NULL ? fmi2Error : fmi2OK; }
FMI2_EXPORT fmi2Status fmi2ExitInitializationMode(fmi2Component c) { return c == NULL ? fmi2Error : fmi2OK; }
FMI2_EXPORT fmi2Status fmi2Terminate(fmi2Component c) { return c == NULL ? fmi2Error : fmi2OK; }
FMI2_EXPORT fmi2Status fmi2Reset(fmi2Component c) {
    SineInstance* instance = (SineInstance*)c;
    if (instance == NULL) { return fmi2Error; }
    instance->amplitude = 1.0; instance->frequency_hz = 1.0; instance->phase_rad = 0.0; instance->offset = 0.0; instance->time = 0.0;
    return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2SetReal(fmi2Component c, const fmi2ValueReference refs[], size_t count, const fmi2Real values[]) {
    SineInstance* instance = (SineInstance*)c;
    size_t index;
    if (instance == NULL) { return fmi2Error; }
    for (index = 0; index < count; ++index) {
        if (refs[index] == VR_AMPLITUDE) { instance->amplitude = values[index]; }
        else if (refs[index] == VR_FREQUENCY_HZ) { instance->frequency_hz = values[index]; }
        else if (refs[index] == VR_PHASE_RAD) { instance->phase_rad = values[index]; }
        else if (refs[index] == VR_OFFSET) { instance->offset = values[index]; }
        else { return fmi2Error; }
    }
    return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2GetReal(fmi2Component c, const fmi2ValueReference refs[], size_t count, fmi2Real values[]) {
    SineInstance* instance = (SineInstance*)c;
    size_t index;
    if (instance == NULL) { return fmi2Error; }
    for (index = 0; index < count; ++index) {
        if (refs[index] == VR_AMPLITUDE) { values[index] = instance->amplitude; }
        else if (refs[index] == VR_FREQUENCY_HZ) { values[index] = instance->frequency_hz; }
        else if (refs[index] == VR_PHASE_RAD) { values[index] = instance->phase_rad; }
        else if (refs[index] == VR_OFFSET) { values[index] = instance->offset; }
        else if (refs[index] == VR_Y) { values[index] = sine_output(instance); }
        else { return fmi2Error; }
    }
    return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2DoStep(fmi2Component c, fmi2Real point, fmi2Real size, fmi2Boolean no_prior_state) {
    SineInstance* instance = (SineInstance*)c;
    (void)no_prior_state;
    if (instance == NULL) { return fmi2Error; }
    instance->time = point + size;
    return fmi2OK;
}

/* v1 exposes scalar Real only; these required FMI 2 entry points explicitly reject unsupported features. */
FMI2_EXPORT fmi2Status fmi2GetInteger(fmi2Component c, const fmi2ValueReference v[], size_t n, fmi2Integer x[]) { (void)c; (void)v; (void)n; (void)x; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2GetBoolean(fmi2Component c, const fmi2ValueReference v[], size_t n, fmi2Boolean x[]) { (void)c; (void)v; (void)n; (void)x; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2GetString(fmi2Component c, const fmi2ValueReference v[], size_t n, fmi2String x[]) { (void)c; (void)v; (void)n; (void)x; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2SetInteger(fmi2Component c, const fmi2ValueReference v[], size_t n, const fmi2Integer x[]) { (void)c; (void)v; (void)n; (void)x; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2SetBoolean(fmi2Component c, const fmi2ValueReference v[], size_t n, const fmi2Boolean x[]) { (void)c; (void)v; (void)n; (void)x; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2SetString(fmi2Component c, const fmi2ValueReference v[], size_t n, const fmi2String x[]) { (void)c; (void)v; (void)n; (void)x; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2GetFMUstate(fmi2Component c, fmi2FMUstate* state) { (void)c; if (state != NULL) { *state = NULL; } return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2SetFMUstate(fmi2Component c, fmi2FMUstate state) { (void)c; (void)state; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2FreeFMUstate(fmi2Component c, fmi2FMUstate* state) { (void)c; if (state != NULL) { *state = NULL; } return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2SerializedFMUstateSize(fmi2Component c, fmi2FMUstate state, size_t* size) { (void)c; (void)state; if (size != NULL) { *size = 0; } return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2SerializeFMUstate(fmi2Component c, fmi2FMUstate state, fmi2Byte bytes[], size_t size) { (void)c; (void)state; (void)bytes; (void)size; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2DeSerializeFMUstate(fmi2Component c, const fmi2Byte bytes[], size_t size, fmi2FMUstate* state) { (void)c; (void)bytes; (void)size; if (state != NULL) { *state = NULL; } return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2GetDirectionalDerivative(fmi2Component c, const fmi2ValueReference u[], size_t nu, const fmi2ValueReference k[], size_t nk, const fmi2Real dk[], fmi2Real du[]) { (void)c; (void)u; (void)nu; (void)k; (void)nk; (void)dk; (void)du; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2SetRealInputDerivatives(fmi2Component c, const fmi2ValueReference v[], size_t n, const fmi2Integer o[], const fmi2Real x[]) { (void)c; (void)v; (void)n; (void)o; (void)x; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2GetRealOutputDerivatives(fmi2Component c, const fmi2ValueReference v[], size_t n, const fmi2Integer o[], fmi2Real x[]) { (void)c; (void)v; (void)n; (void)o; (void)x; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2CancelStep(fmi2Component c) { return c == NULL ? fmi2Error : fmi2OK; }
FMI2_EXPORT fmi2Status fmi2GetStatus(fmi2Component c, fmi2StatusKind kind, fmi2Status* value) { (void)kind; if (c == NULL || value == NULL) { return fmi2Error; } *value = fmi2OK; return fmi2OK; }
FMI2_EXPORT fmi2Status fmi2GetRealStatus(fmi2Component c, fmi2StatusKind kind, fmi2Real* value) { (void)kind; if (c == NULL || value == NULL) { return fmi2Error; } *value = 0.0; return fmi2OK; }
FMI2_EXPORT fmi2Status fmi2GetIntegerStatus(fmi2Component c, fmi2StatusKind kind, fmi2Integer* value) { (void)kind; if (c == NULL || value == NULL) { return fmi2Error; } *value = 0; return fmi2OK; }
FMI2_EXPORT fmi2Status fmi2GetBooleanStatus(fmi2Component c, fmi2StatusKind kind, fmi2Boolean* value) { (void)kind; if (c == NULL || value == NULL) { return fmi2Error; } *value = 0; return fmi2OK; }
FMI2_EXPORT fmi2Status fmi2GetStringStatus(fmi2Component c, fmi2StatusKind kind, fmi2String* value) { (void)kind; if (c == NULL || value == NULL) { return fmi2Error; } *value = ""; return fmi2OK; }
