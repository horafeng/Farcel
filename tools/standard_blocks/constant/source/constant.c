/*
 * Farcel Standard Block Library -- Constant FMI 2.0 Co-Simulation FMU.
 * SPDX-License-Identifier: MIT
 */

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
enum { VR_VALUE = 0, VR_Y = 1 };

typedef struct {
    fmi2Real value;
} ConstantInstance;

FMI2_EXPORT const char* fmi2GetTypesPlatform(void) { return "default"; }
FMI2_EXPORT const char* fmi2GetVersion(void) { return "2.0"; }

FMI2_EXPORT fmi2Status fmi2SetDebugLogging(
    fmi2Component component,
    fmi2Boolean logging_on,
    size_t category_count,
    const fmi2String categories[]
) {
    (void)component;
    (void)logging_on;
    (void)category_count;
    (void)categories;
    return fmi2OK;
}

FMI2_EXPORT fmi2Component fmi2Instantiate(
    fmi2String instance_name,
    fmi2Type fmu_type,
    fmi2String guid,
    fmi2String resource_location,
    const fmi2CallbackFunctions* callbacks,
    fmi2Boolean visible,
    fmi2Boolean logging_on
) {
    ConstantInstance* instance;
    (void)instance_name;
    (void)guid;
    (void)resource_location;
    (void)callbacks;
    (void)visible;
    (void)logging_on;
    if (fmu_type != fmi2CoSimulation) {
        return NULL;
    }
    instance = (ConstantInstance*)calloc(1, sizeof(ConstantInstance));
    if (instance != NULL) {
        instance->value = 1.0;
    }
    return instance;
}

FMI2_EXPORT void fmi2FreeInstance(fmi2Component component) { free(component); }

FMI2_EXPORT fmi2Status fmi2SetupExperiment(
    fmi2Component component,
    fmi2Boolean tolerance_defined,
    fmi2Real tolerance,
    fmi2Real start_time,
    fmi2Boolean stop_time_defined,
    fmi2Real stop_time
) {
    (void)component;
    (void)tolerance_defined;
    (void)tolerance;
    (void)start_time;
    (void)stop_time_defined;
    (void)stop_time;
    return fmi2OK;
}

FMI2_EXPORT fmi2Status fmi2EnterInitializationMode(fmi2Component component) {
    return component == NULL ? fmi2Error : fmi2OK;
}

FMI2_EXPORT fmi2Status fmi2ExitInitializationMode(fmi2Component component) {
    return component == NULL ? fmi2Error : fmi2OK;
}

FMI2_EXPORT fmi2Status fmi2Terminate(fmi2Component component) {
    return component == NULL ? fmi2Error : fmi2OK;
}

FMI2_EXPORT fmi2Status fmi2Reset(fmi2Component component) {
    ConstantInstance* instance = (ConstantInstance*)component;
    if (instance == NULL) {
        return fmi2Error;
    }
    instance->value = 1.0;
    return fmi2OK;
}

FMI2_EXPORT fmi2Status fmi2SetReal(
    fmi2Component component,
    const fmi2ValueReference value_references[],
    size_t value_count,
    const fmi2Real values[]
) {
    ConstantInstance* instance = (ConstantInstance*)component;
    size_t index;
    if (instance == NULL) {
        return fmi2Error;
    }
    for (index = 0; index < value_count; ++index) {
        if (value_references[index] != VR_VALUE) {
            return fmi2Error;
        }
        instance->value = values[index];
    }
    return fmi2OK;
}

FMI2_EXPORT fmi2Status fmi2GetReal(
    fmi2Component component,
    const fmi2ValueReference value_references[],
    size_t value_count,
    fmi2Real values[]
) {
    ConstantInstance* instance = (ConstantInstance*)component;
    size_t index;
    if (instance == NULL) {
        return fmi2Error;
    }
    for (index = 0; index < value_count; ++index) {
        if (value_references[index] != VR_VALUE && value_references[index] != VR_Y) {
            return fmi2Error;
        }
        values[index] = instance->value;
    }
    return fmi2OK;
}

/* Constant exposes only scalar Real variables. The remaining FMI 2 APIs are
 * present so a conforming Co-Simulation host can load the complete interface. */
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

FMI2_EXPORT fmi2Status fmi2DoStep(
    fmi2Component component,
    fmi2Real current_communication_point,
    fmi2Real communication_step_size,
    fmi2Boolean no_set_fmu_state_prior_to_current_point
) {
    (void)current_communication_point;
    (void)communication_step_size;
    (void)no_set_fmu_state_prior_to_current_point;
    return component == NULL ? fmi2Error : fmi2OK;
}
