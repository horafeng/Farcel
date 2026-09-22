/* Farcel Standard Block Library -- First Order FMI 2.0 Co-Simulation FMU. SPDX-License-Identifier: MIT */
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
enum { VR_GAIN = 0, VR_TIME_CONSTANT = 1, VR_INITIAL_VALUE = 2, VR_U = 3, VR_Y = 4 };

typedef struct {
    fmi2Real gain;
    fmi2Real time_constant;
    fmi2Real initial_value;
    fmi2Real u;
    fmi2Real y;
} FirstOrderInstance;

FMI2_EXPORT const char* fmi2GetTypesPlatform(void) { return "default"; }
FMI2_EXPORT const char* fmi2GetVersion(void) { return "2.0"; }
FMI2_EXPORT fmi2Status fmi2SetDebugLogging(fmi2Component c, fmi2Boolean l, size_t n, const fmi2String x[]) { (void)c; (void)l; (void)n; (void)x; return fmi2OK; }
FMI2_EXPORT fmi2Component fmi2Instantiate(fmi2String name, fmi2Type type, fmi2String guid, fmi2String resource, const fmi2CallbackFunctions* callbacks, fmi2Boolean visible, fmi2Boolean logging) {
    FirstOrderInstance* instance;
    (void)name; (void)guid; (void)resource; (void)callbacks; (void)visible; (void)logging;
    if (type != fmi2CoSimulation) return NULL;
    instance = (FirstOrderInstance*)calloc(1, sizeof(FirstOrderInstance));
    if (instance != NULL) { instance->gain = 1.0; instance->time_constant = 1.0; }
    return instance;
}
FMI2_EXPORT void fmi2FreeInstance(fmi2Component c) { free(c); }
FMI2_EXPORT fmi2Status fmi2SetupExperiment(fmi2Component c, fmi2Boolean td, fmi2Real t, fmi2Real start, fmi2Boolean sd, fmi2Real stop) { (void)td; (void)t; (void)start; (void)sd; (void)stop; return c == NULL ? fmi2Error : fmi2OK; }
FMI2_EXPORT fmi2Status fmi2EnterInitializationMode(fmi2Component c) { return c == NULL ? fmi2Error : fmi2OK; }
FMI2_EXPORT fmi2Status fmi2ExitInitializationMode(fmi2Component c) {
    FirstOrderInstance* instance = (FirstOrderInstance*)c;
    if (instance == NULL || instance->time_constant <= 0.0) return fmi2Error;
    instance->y = instance->initial_value;
    return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2Terminate(fmi2Component c) { return c == NULL ? fmi2Error : fmi2OK; }
FMI2_EXPORT fmi2Status fmi2Reset(fmi2Component c) {
    FirstOrderInstance* x = (FirstOrderInstance*)c;
    if (!x) return fmi2Error;
    x->gain = 1.0; x->time_constant = 1.0; x->initial_value = 0.0; x->u = 0.0; x->y = 0.0;
    return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2SetReal(fmi2Component c, const fmi2ValueReference refs[], size_t n, const fmi2Real values[]) {
    FirstOrderInstance* x = (FirstOrderInstance*)c; size_t i;
    if (!x) return fmi2Error;
    for (i = 0; i < n; ++i) {
        if (refs[i] == VR_GAIN) x->gain = values[i];
        else if (refs[i] == VR_TIME_CONSTANT) x->time_constant = values[i];
        else if (refs[i] == VR_INITIAL_VALUE) x->initial_value = values[i];
        else if (refs[i] == VR_U) x->u = values[i];
        else return fmi2Error;
    }
    return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2GetReal(fmi2Component c, const fmi2ValueReference refs[], size_t n, fmi2Real values[]) {
    FirstOrderInstance* x = (FirstOrderInstance*)c; size_t i;
    if (!x) return fmi2Error;
    for (i = 0; i < n; ++i) {
        if (refs[i] == VR_GAIN) values[i] = x->gain;
        else if (refs[i] == VR_TIME_CONSTANT) values[i] = x->time_constant;
        else if (refs[i] == VR_INITIAL_VALUE) values[i] = x->initial_value;
        else if (refs[i] == VR_U) values[i] = x->u;
        else if (refs[i] == VR_Y) values[i] = x->y;
        else return fmi2Error;
    }
    return fmi2OK;
}
FMI2_EXPORT fmi2Status fmi2DoStep(fmi2Component c, fmi2Real point, fmi2Real step, fmi2Boolean no_prior_state) {
    FirstOrderInstance* x = (FirstOrderInstance*)c;
    fmi2Real target;
    fmi2Real decay;
    (void)point; (void)no_prior_state;
    if (x == NULL || x->time_constant <= 0.0 || step < 0.0) return fmi2Error;
    target = x->gain * x->u;
    decay = exp(-step / x->time_constant);
    x->y = target + (x->y - target) * decay;
    return fmi2OK;
}

/* v1 exposes scalar Real only; unsupported FMI 2 operations fail explicitly. */
FMI2_EXPORT fmi2Status fmi2GetInteger(fmi2Component c, const fmi2ValueReference v[], size_t n, fmi2Integer x[]) { (void)c; (void)v; (void)n; (void)x; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2GetBoolean(fmi2Component c, const fmi2ValueReference v[], size_t n, fmi2Boolean x[]) { (void)c; (void)v; (void)n; (void)x; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2GetString(fmi2Component c, const fmi2ValueReference v[], size_t n, fmi2String x[]) { (void)c; (void)v; (void)n; (void)x; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2SetInteger(fmi2Component c, const fmi2ValueReference v[], size_t n, const fmi2Integer x[]) { (void)c; (void)v; (void)n; (void)x; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2SetBoolean(fmi2Component c, const fmi2ValueReference v[], size_t n, const fmi2Boolean x[]) { (void)c; (void)v; (void)n; (void)x; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2SetString(fmi2Component c, const fmi2ValueReference v[], size_t n, const fmi2String x[]) { (void)c; (void)v; (void)n; (void)x; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2GetFMUstate(fmi2Component c, fmi2FMUstate* x) { (void)c; if (x) *x = NULL; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2SetFMUstate(fmi2Component c, fmi2FMUstate x) { (void)c; (void)x; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2FreeFMUstate(fmi2Component c, fmi2FMUstate* x) { (void)c; if (x) *x = NULL; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2SerializedFMUstateSize(fmi2Component c, fmi2FMUstate x, size_t* n) { (void)c; (void)x; if (n) *n = 0; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2SerializeFMUstate(fmi2Component c, fmi2FMUstate x, fmi2Byte b[], size_t n) { (void)c; (void)x; (void)b; (void)n; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2DeSerializeFMUstate(fmi2Component c, const fmi2Byte b[], size_t n, fmi2FMUstate* x) { (void)c; (void)b; (void)n; if (x) *x = NULL; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2GetDirectionalDerivative(fmi2Component c, const fmi2ValueReference a[], size_t na, const fmi2ValueReference b[], size_t nb, const fmi2Real d[], fmi2Real r[]) { (void)c; (void)a; (void)na; (void)b; (void)nb; (void)d; (void)r; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2SetRealInputDerivatives(fmi2Component c, const fmi2ValueReference v[], size_t n, const fmi2Integer o[], const fmi2Real x[]) { (void)c; (void)v; (void)n; (void)o; (void)x; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2GetRealOutputDerivatives(fmi2Component c, const fmi2ValueReference v[], size_t n, const fmi2Integer o[], fmi2Real x[]) { (void)c; (void)v; (void)n; (void)o; (void)x; return fmi2Error; }
FMI2_EXPORT fmi2Status fmi2CancelStep(fmi2Component c) { return c == NULL ? fmi2Error : fmi2OK; }
FMI2_EXPORT fmi2Status fmi2GetStatus(fmi2Component c, fmi2StatusKind k, fmi2Status* x) { (void)k; if (!c || !x) return fmi2Error; *x = fmi2OK; return fmi2OK; }
FMI2_EXPORT fmi2Status fmi2GetRealStatus(fmi2Component c, fmi2StatusKind k, fmi2Real* x) { (void)k; if (!c || !x) return fmi2Error; *x = 0.0; return fmi2OK; }
FMI2_EXPORT fmi2Status fmi2GetIntegerStatus(fmi2Component c, fmi2StatusKind k, fmi2Integer* x) { (void)k; if (!c || !x) return fmi2Error; *x = 0; return fmi2OK; }
FMI2_EXPORT fmi2Status fmi2GetBooleanStatus(fmi2Component c, fmi2StatusKind k, fmi2Boolean* x) { (void)k; if (!c || !x) return fmi2Error; *x = 0; return fmi2OK; }
FMI2_EXPORT fmi2Status fmi2GetStringStatus(fmi2Component c, fmi2StatusKind k, fmi2String* x) { (void)k; if (!c || !x) return fmi2Error; *x = ""; return fmi2OK; }
