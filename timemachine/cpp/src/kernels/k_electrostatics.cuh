#include "surreal.cuh"
#include "../fixed_point.hpp"
#include "kernel_utils.cuh"
#define WARPSIZE 32

template <typename RealType>
void __global__ k_electrostatics_inference(
    const int N,
    const double *coords,
    const double lambda,
    const int *lambda_plane_idxs, // 0 or 1, which non-interacting plane we're on
    const int *lambda_offset_idxs, // 0 or 1, how much we offset from the plane by cutoff
    const double *charge_params, // [N]
    const double cutoff,
    const double *block_bounds_ctr,
    const double *block_bounds_ext,
    unsigned long long *grad_coords,
    double *out_du_dl,
    double *out_energy) {

    if(blockIdx.y > blockIdx.x) {
        return;
    }

    RealType block_d2ij = 0; 
    for(int d=0; d < 3; d++) {
        RealType block_row_ctr = block_bounds_ctr[blockIdx.x*3+d];
        RealType block_col_ctr = block_bounds_ctr[blockIdx.y*3+d];
        RealType block_row_ext = block_bounds_ext[blockIdx.x*3+d];
        RealType block_col_ext = block_bounds_ext[blockIdx.y*3+d];
        RealType dx = max(0.0, fabs(block_row_ctr-block_col_ctr) - (block_row_ext+block_col_ext));
        block_d2ij += dx*dx;
    }

    if(block_d2ij > cutoff*cutoff) {
        return;
    }

    int atom_i_idx = blockIdx.x*32 + threadIdx.x;
    int lambda_plane_i = 0;
    int lambda_offset_i = 0;

    if(atom_i_idx < N) {
        lambda_plane_i = lambda_plane_idxs[atom_i_idx];
        lambda_offset_i = lambda_offset_idxs[atom_i_idx];
    }

    RealType ci[3];
    RealType gi[3] = {0};
    RealType du_dl_i = 0;
    for(int d=0; d < 3; d++) {
        ci[d] = atom_i_idx < N ? coords[atom_i_idx*3+d] : 0;
    }
    
    int charge_param_idx_i = atom_i_idx < N ? atom_i_idx : 0;

    RealType qi = atom_i_idx < N ? charge_params[charge_param_idx_i] : 0;

    int atom_j_idx = blockIdx.y*32 + threadIdx.x;
    int lambda_plane_j = 0;
    int lambda_offset_j = 0;

    if(atom_j_idx < N) {
        lambda_plane_j = lambda_plane_idxs[atom_j_idx];
        lambda_offset_j = lambda_offset_idxs[atom_j_idx];
    }

    RealType cj[3];
    RealType gj[3] = {0};
    RealType du_dl_j = 0;
    for(int d=0; d < 3; d++) {
        cj[d] = atom_j_idx < N ? coords[atom_j_idx*3+d] : 0;
    }

    int charge_param_idx_j = atom_j_idx < N ? atom_j_idx : 0;

    RealType qj = atom_j_idx < N ? charge_params[charge_param_idx_j] : 0;
    RealType inv_cutoff = 1/cutoff;

    // revert this to RealType

    // tbd: deprecate this when we don't need energies any more.
    double energy = 0; // spit this into three parts? (es, lj close, lj far?)

    // In inference mode, we don't care about gradients with respect to parameters.
    for(int round = 0; round < 32; round++) {

        RealType dxs[4];
        for(int d=0; d < 3; d++) {
            dxs[d] = ci[d] - cj[d];
        }

        // we can optimize this later if need be
        RealType delta_lambda = (lambda_plane_i - lambda_plane_j)*cutoff + (lambda_offset_i - lambda_offset_j)*lambda;

        dxs[3] = delta_lambda;

        RealType inv_dij = fast_vec_rnorm<RealType, 4>(dxs);

        if(atom_j_idx < atom_i_idx && inv_dij > inv_cutoff && atom_j_idx < N && atom_i_idx < N) {

            RealType inv_d2ij = inv_dij*inv_dij;
            RealType inv_d3ij = inv_dij*inv_d2ij;
            RealType es_grad_prefactor = qi*qj*inv_d3ij;

            for(int d=0; d < 3; d++) {

                RealType force_i = es_grad_prefactor *  dxs[d];
                RealType force_j = es_grad_prefactor *  dxs[d];

                gi[d] -= force_i;
                gj[d] += force_j;

            }

            // this technically should be if lambda_idxs[i] == 0 and lamba_idxs[j] == 0
            // however, they both imply that delta_lambda = 0, so dxs[3] == 0, simplifying the equation
            int dw_i = lambda_offset_i;
            int dw_j = lambda_offset_j;

            du_dl_i -= es_grad_prefactor * dxs[3] * dw_i;
            du_dl_j += es_grad_prefactor * dxs[3] * dw_j;

            energy += qi*qj*inv_dij;
        }

        const int srcLane = (threadIdx.x + 1) % WARPSIZE; // fixed
        atom_j_idx = __shfl_sync(0xffffffff, atom_j_idx, srcLane);
        qj = __shfl_sync(0xffffffff, qj, srcLane);
        for(size_t d=0; d < 3; d++) {
            cj[d] = __shfl_sync(0xffffffff, cj[d], srcLane); // needs to support real
            gj[d] = __shfl_sync(0xffffffff, gj[d], srcLane);
        }
        lambda_plane_j = __shfl_sync(0xffffffff, lambda_plane_j, srcLane);
        lambda_offset_j = __shfl_sync(0xffffffff, lambda_offset_j, srcLane);
        du_dl_j = __shfl_sync(0xffffffff, du_dl_j, srcLane);
    }

    for(int d=0; d < 3; d++) {

        if(atom_i_idx < N) {
            atomicAdd(grad_coords + atom_i_idx*3 + d, static_cast<unsigned long long>((long long) (gi[d]*FIXED_EXPONENT)));            
        }

        if(atom_j_idx < N) {
            atomicAdd(grad_coords + atom_j_idx*3 + d, static_cast<unsigned long long>((long long) (gj[d]*FIXED_EXPONENT)));            
        }
    }

    atomicAdd(out_du_dl, du_dl_i + du_dl_j);
    atomicAdd(out_energy, energy);

}

template<typename RealType>
void __global__ k_electrostatics_exclusion_inference(
    const int E, // number of exclusions
    const double *coords,
    const double lambda,
    const int *lambda_plane_idxs, // 0 or 1, which non-interacting plane we're on
    const int *lambda_offset_idxs, // 0 or 1, how much we offset from the plane by cutoff
    const int *exclusion_idxs, // [E, 2]pair-list of atoms to be excluded
    const double *charge_scales, // [E]
    const double *charge_params, // [N]
    const double cutoff,
    unsigned long long *grad_coords,
    double *out_du_dl,
    double *out_energy) {

    const int e_idx = blockIdx.x*blockDim.x + threadIdx.x;
    if(e_idx >= E) {
        return;
    }

    int atom_i_idx = exclusion_idxs[e_idx*2 + 0];
    RealType du_dl_i = 0;

    int lambda_plane_i = lambda_plane_idxs[atom_i_idx];
    int lambda_offset_i = lambda_offset_idxs[atom_i_idx];

    RealType ci[3];
    double gi[3] = {0};
    #pragma unroll
    for(int d=0; d < 3; d++) {
        ci[d] = coords[atom_i_idx*3+d];
    }

    int charge_param_idx_i = atom_i_idx;

    RealType qi = charge_params[charge_param_idx_i];

    int atom_j_idx = exclusion_idxs[e_idx*2 + 1];

    RealType du_dl_j = 0;

    int lambda_plane_j = lambda_plane_idxs[atom_j_idx];
    int lambda_offset_j = lambda_offset_idxs[atom_j_idx];

    RealType cj[3];
    double gj[3] = {0};
    #pragma unroll
    for(int d=0; d < 3; d++) {
        cj[d] = coords[atom_j_idx*3+d];
    }

    int charge_param_idx_j = atom_j_idx;
    RealType qj = charge_params[charge_param_idx_j];
    RealType charge_scale = charge_scales[e_idx];

    RealType dxs[4];
    for(int d=0; d < 3; d++) {
        dxs[d] = ci[d] - cj[d];
    }

    RealType delta_lambda = cutoff*(lambda_plane_i - lambda_plane_j) + lambda*(lambda_offset_i - lambda_offset_j);
    dxs[3] = delta_lambda;

    RealType inv_dij = fast_vec_rnorm<RealType, 4>(dxs);
    RealType inv_cutoff = 1/cutoff;

    if(inv_dij > inv_cutoff) {

        RealType inv_d2ij = inv_dij*inv_dij;
        RealType inv_d3ij = inv_dij*inv_d2ij;
        RealType es_grad_prefactor = qi*qj*inv_d3ij;

        #pragma unroll
        for(int d=0; d < 3; d++) {
            gi[d] += charge_scale*es_grad_prefactor*dxs[d];
            gj[d] -= charge_scale*es_grad_prefactor*dxs[d];
        }

        int dw_i = lambda_offset_i;
        int dw_j = lambda_offset_j;

        du_dl_i += charge_scale * es_grad_prefactor * dxs[3] * dw_i;
        du_dl_j -= charge_scale * es_grad_prefactor * dxs[3] * dw_j;

        for(int d=0; d < 3; d++) {

            atomicAdd(grad_coords + atom_i_idx*3 + d, static_cast<unsigned long long>((long long) (gi[d]*FIXED_EXPONENT)));
            atomicAdd(grad_coords + atom_j_idx*3 + d, static_cast<unsigned long long>((long long) (gj[d]*FIXED_EXPONENT)));

        }  

        atomicAdd(out_du_dl, du_dl_i + du_dl_j);
        RealType energy = charge_scale*qi*qj*inv_dij;
        atomicAdd(out_energy, -energy);
    }

}