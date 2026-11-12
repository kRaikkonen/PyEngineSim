/*
 * enginesim_core.h -- the C ABI of the EngineSim v2 core.
 *
 * docs/REDESIGN_V2.md s.5.3.  Kept in step with core/capi/src/lib.rs by hand;
 * es_abi_version() and es_sizeof_control() let a binding check itself.
 *
 * Threads
 *   es_control          any thread, never waits
 *   es_render, es_run   one thread at a time: the audio thread
 *   everything else     the thread that owns the engine, while nothing renders
 *
 * Units are SI; angles are crank degrees in a cylinder's own four-stroke
 * cycle with 0 at FIRING top dead centre (expansion 0-180, exhaust 180-360,
 * intake 360-540, compression 540-720).
 */
#ifndef ENGINESIM_CORE_H
#define ENGINESIM_CORE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define ES_ABI_VERSION 3u

typedef struct es_engine es_engine;

/* A valve's EFFECTIVE flow area (discharge coefficient included, all of the
 * cylinder's valves of that kind together) against crank angle. */
typedef struct {
    double open_deg;      /* where it opens, [0, 720) */
    double span_deg;      /* how long it is open, (0, 720) */
    const double *area;   /* m^2, n points uniformly from opening to closing; both ends 0 */
    int32_t n;
} es_valve_t;

typedef struct {
    double bore;                /* m */
    double stroke;              /* m */
    double rod;                 /* connecting rod, centre to centre, m */
    double compression;         /* geometric compression ratio */
    const double *firing_deg;   /* each cylinder's firing TDC in the engine's 720 degree cycle */
    int32_t cylinders;          /* 1..64 */
    es_valve_t exhaust;
    es_valve_t intake;
    double wall_temp;           /* K */
    double heat_scale;          /* 1 = Hohenberg as published, 0 = adiabatic walls */
    double gamma_const;         /* 0 = gamma(T); > 0 = this constant */
    double wall_refresh_deg;    /* wall heat-transfer coefficient re-evaluated every so many degrees */
} es_spec_t;

/* One operating point. */
typedef struct {
    double rpm;
    double p_intake;            /* Pa, in the intake port (the manifold) */
    double t_intake;            /* K */
    double p_exhaust;           /* Pa, what the exhaust port discharges into; with an
                                   exhaust network: the air at its open ends */
    double t_exhaust;           /* K, the gas standing in the exhaust port */
    double charge;              /* kg of air trapped per cylinder per cycle; <= 0: breathe */
    double lambda;              /* relative air/fuel ratio */
    double ca50_deg;            /* 50 % burned, degrees after firing TDC */
    double burn_deg;            /* 10-90 % burn duration, degrees */
    double intake_phase_deg;    /* cam phasing: degrees LATER */
    double exhaust_phase_deg;
    double orifice[4];          /* multipliers of the network orifices that name a
                                   channel (turbine vanes, a flap): 1 = as built */
    double ccv;                 /* cycle-to-cycle variation of the burn, about the
                                   coefficient of variation of the indicated work */
    double level_ref;           /* Pa rms at 1 m (ear-weighted) that plays at 0.2 rms
                                   of full scale; <= 0: a fixed 200 Pa = full scale */
    double dynamics;            /* 1 = the real dynamic range, 0 = a constant level */
    double volume;              /* linear */
    uint64_t fuel_cut;          /* bit k: cylinder k gets no fuel */
} es_control_t;

/* An exhaust network: pipes between nodes.  A chamber is a fat pipe, a side
 * branch a pipe with one end closed. */
typedef struct {
    double length;              /* m */
    double area;                /* m^2 */
    double heat;                /* 0 = the air outside .. 1 = the gas in the port */
    double loss_db;             /* absorption beyond a bare wall, dB/m at 1 kHz */
    uint32_t a, b;              /* the nodes at its ends */
} es_pipe_t;

enum { ES_JUNCTION = 0, ES_VALVE = 1, ES_ORIFICE = 2, ES_OPEN = 3 };

typedef struct {
    int32_t kind;               /* ES_JUNCTION: pipe ends at one pressure (one: closed)
                                   ES_VALVE:    where cylinder `index` discharges
                                   ES_ORIFICE:  dp = r q + k q|q|; `index` = control
                                                channel that scales it, or -1
                                   ES_OPEN:     an end of `radius` radiating */
    int32_t index;
    double r;                   /* Pa s/kg */
    double k;                   /* Pa s^2/kg^2 */
    double radius;              /* m */
    double throat;              /* orifice: the passage its flow noise is made in, m^2 */
    double noise;               /* orifice, open end: rms fluctuation of the turbulent
                                   loss, as a fraction of it */
    double distance;            /* open end: metres from the listener (<= 0: 1 m) */
} es_node_t;

/* One cylinder's state and running totals. */
typedef struct {
    double p, t, m, m_burned, v;      /* Pa, K, kg, kg, m^3 */
    double q_exhaust, q_intake;       /* last sample's flows, kg/s */
    double work, burn, wall;          /* J since es_clear_ledgers */
    double m_exhaust, m_intake;       /* kg since es_clear_ledgers */
} es_cylinder_t;

uint32_t es_abi_version(void);
uint32_t es_sizeof_control(void);

/* Returns NULL if the spec is refused.  The spec is copied. */
es_engine *es_open(const es_spec_t *spec, double sample_rate);
void es_close(es_engine *e);

/* Publish an operating point; it is ramped in over the next 32 samples.
 * Returns 1, or 0 if another thread was publishing at that very moment. */
int32_t es_control(const es_engine *e, const es_control_t *c);

/* Give the exhaust valves a network to discharge into.  Returns 1, or 0 if
 * it is refused (the engine is then unchanged).  `valve_noise`: rms
 * fluctuation of the flow through a valve seat, as a fraction. */
int32_t es_attach_exhaust(es_engine *e, const es_pipe_t *pipes, int32_t n_pipes,
                          const es_node_t *nodes, int32_t n_nodes,
                          double t_ambient, double valve_noise);

/* Render n frames.  No allocation, no lock, no system call.
 * The sound pressure 1 m from the open ends of the exhaust (with no network:
 * from the bare exhaust valves), through the loudness curve and a limiter
 * that only protects.  Exhaust only so far: no intake, no engine block. */
void es_render(es_engine *e, float *left, float *right, int32_t n);

/* Jump to an operating point and run `cycles` engine cycles silently. */
void es_prime(es_engine *e, const es_control_t *c, double cycles);

/* The instrument tap: advance n samples exactly as es_render does, writing
 * the valves' flows (kg/s, all cylinders) and cylinder `cyl`'s pressure,
 * temperature and mass.  Any output may be NULL. */
void es_run(es_engine *e, int32_t n, int32_t cyl,
            double *q_exhaust, double *q_intake, double *p, double *t, double *m);

/* The tap on the exhaust: as es_run, writing the flow through the exhaust
 * valves and out of the open ends (kg/s), the sound pressure 1 m from the
 * open ends (Pa) and the pressure at the upstream end of pipe `pipe` (Pa
 * about the outside).  Any output may be NULL. */
void es_listen(es_engine *e, int32_t n, int32_t pipe,
               double *q_exhaust, double *q_tail, double *p_far, double *p_pipe);

/* The level the loudness curve reads now: Pa rms at 1 m, ear-weighted. */
double es_level(const es_engine *e);

int32_t es_cylinder(const es_engine *e, int32_t cyl, es_cylinder_t *out);
void es_clear_ledgers(es_engine *e);
double es_crank_deg(const es_engine *e);

/* What the ports present to the valves, Pa s/kg (0 = a plenum): the seam the
 * exhaust network attaches to. */
void es_set_port_impedance(es_engine *e, double exhaust_z, double intake_z);

#ifdef __cplusplus
}
#endif

#endif /* ENGINESIM_CORE_H */
