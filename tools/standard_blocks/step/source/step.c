/* Farcel Standard Block Library -- Step FMI 2.0 Co-Simulation FMU. SPDX-License-Identifier: MIT */
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
enum { VR_INITIAL_VALUE = 0, VR_FINAL_VALUE = 1, VR_STEP_TIME = 2, VR_Y = 3 };

typedef struct {
    fmi2Real initial_value;
    fmi2Real final_value;
    fmi2Real step_time;
    fmi2Real time;
} StepInstance;

static fmi2Real step_output(const StepInstance* instance) {
    return instance->time < instance->step_time
        ? instance->initial_value
        : instance->final_value;
}

FMI2_EXPORT const char* fmi2GetTypesPlatform(void) { return "default"; }
FMI2_EXPORT const char* fmi2GetVersion(void) { return "2.0"; }
FMI2_EXPORT fmi2Status fmi2SetDebugLogging(fmi2Component c, fmi2Boolean logging, size_t count, const fmi2String categories[]) { (void)c; (void)logging; (void)count; (void)categories; return fmi2OK; }
FMI2_EXPORT fmi2Component fmi2Instantiate(fmi2String name, fmi2Type type, fmi2String guid, fmi2String resource, const fmi2CallbackFunctions* callbacks, fmi2Boolean visible, fmi2Boolean logging) {
    StepInstance* instance;
    (void)name; (void)guid; (void)resource; (void)callbacks; (void)visible; (void)logging;
    if (type != fmi2CoSimulation) { return NULL; }
    instance = (StepInstance*)calloc(1, sizeof(StepInstance));
    if (instance != NULL) { instance->final_value = 1.0; instance->step_time = 1.0; }
    return instance;
}
FMI2_EXPORT void fmi2FreeInstance(fmi2Component c) { free(c); }
FMI2_EXPORT fmi2Status fmi2SetupExperiment(fmi2Component c, fmi2Boolean tolerance_defined, fmi2Real tolerance, fmi2Real start_time, fmi2Boolean stop_time_defined, fmi2Real stop_time) {
    StepInstance* instance = (StepInstance*)c;
    (void)tolerance_defined; (void)tolerance; (void)stop_time_defined; (void)stop_time;
    if (instance == NULL) { return fmi2Error; }
    instance->time = start_time;
    return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2EnterInitializationMode(fmi2Component c) { return c == NULL ? fmi2Error : fmi2OK; }
FMI2_EXPORT fmi2Status fmi2ExitInitializationMode(fmi2Component c) { return c == NULL ? fmi2Error : fmi2OK; }
FMI2_EXPORT fmi2Status fmi2Terminate(fmi2Component c) { return c == NULL ? fmi2Error : fmi2OK; }
FMI2_EXPORT fmi2Status fmi2Reset(fmi2Component c) {
    StepInstance* instance = (StepInstance*)c;
    if (instance == NULL) { return fmi2Error; }
    instance->initial_value = 0.0; instance->final_value = 1.0; instance->step_time = 1.0; instance->time = 0.0;
    return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2SetReal(fmi2Component c, const fmi2ValueReference refs[], size_t count, const fmi2Real values[]) {
    StepInstance* instance = (StepInstance*)c;
    size_t index;
    if (instance == NULL) { return fmi2Error; }
    for (index = 0; index < count; ++index) {
        if (refs[index] == VR_INITIAL_VALUE) { instance->initial_value = values[index]; }
        else if (refs[index] == VR_FINAL_VALUE) { instance->final_value = values[index]; }
        else if (refs[index] == VR_STEP_TIME) { instance->step_time = values[index]; }
        else { return fmi2Error; }
    }
    return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2GetReal(fmi2Component c, const fmi2ValueReference refs[], size_t count, fmi2Real values[]) {
    StepInstance* instance = (StepInstance*)c;
    size_t index;
    if (instance == NULL) { return fmi2Error; }
    for (index = 0; index < count; ++index) {
        if (refs[index] == VR_INITIAL_VALUE) { values[index] = instance->initial_value; }
        else if (refs[index] == VR_FINAL_VALUE) { values[index] = instance->final_value; }
        else if (refs[index] == VR_STEP_TIME) { values[index] = instance->step_time; }
        else if (refs[index] == VR_Y) { values[index] = step_output(instance); }
        else { return fmi2Error; }
    }
    return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2DoStep(fmi2Component c, fmi2Real point, fmi2Real size, fmi2Boolean no_prior_state) {
    StepInstance* instance = (StepInstance*)c;
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
