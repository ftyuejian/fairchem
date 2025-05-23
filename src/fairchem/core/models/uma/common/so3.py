"""
Copyright (c) Meta Platforms, Inc. and affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""

from __future__ import annotations

import math

import torch
from e3nn.o3 import FromS2Grid, ToS2Grid


class CoefficientMapping(torch.nn.Module):
    """
    Helper module for coefficients used to reshape l <--> m and to get coefficients of specific degree or order

    Args:
        lmax_list (list:int):   List of maximum degree of the spherical harmonics
        mmax_list (list:int):   List of maximum order of the spherical harmonics
        use_rotate_inv_rescale (bool):  Whether to pre-compute inverse rotation rescale matrices
    """

    def __init__(
        self,
        lmax,
        mmax,
    ):
        super().__init__()

        self.lmax = lmax
        self.mmax = mmax
        # Compute the degree (l) and order (m) for each entry of the embedding
        l_harmonic = torch.tensor([]).long()
        m_harmonic = torch.tensor([]).long()
        m_complex = torch.tensor([]).long()

        for l in range(self.lmax + 1):
            mmax = min(self.mmax, l)
            m = torch.arange(-mmax, mmax + 1).long()
            m_complex = torch.cat([m_complex, m], dim=0)
            m_harmonic = torch.cat([m_harmonic, torch.abs(m).long()], dim=0)
            l_harmonic = torch.cat([l_harmonic, m.fill_(l).long()], dim=0)
        self.res_size = len(l_harmonic)

        num_coefficients = len(l_harmonic)
        # `self.to_m` moves m components from different L to contiguous index
        to_m = torch.zeros([num_coefficients, num_coefficients])
        self.m_size = torch.zeros([self.mmax + 1]).long().tolist()

        offset = 0
        for m in range(self.mmax + 1):
            idx_r, idx_i = self.complex_idx(m, -1, m_complex, l_harmonic)

            for idx_out, idx_in in enumerate(idx_r):
                to_m[idx_out + offset, idx_in] = 1.0
            offset = offset + len(idx_r)

            self.m_size[m] = len(idx_r)

            for idx_out, idx_in in enumerate(idx_i):
                to_m[idx_out + offset, idx_in] = 1.0
            offset = offset + len(idx_i)

        to_m = to_m.detach()

        # save tensors and they will be moved to GPU
        self.register_buffer("l_harmonic", l_harmonic, persistent=False)
        self.register_buffer("m_harmonic", m_harmonic, persistent=False)
        self.register_buffer("m_complex", m_complex, persistent=False)
        self.register_buffer("to_m", to_m, persistent=False)

        self.pre_compute_coefficient_idx()

    # Return mask containing coefficients of order m (real and imaginary parts)
    def complex_idx(self, m, lmax, m_complex, l_harmonic):
        """
        Add `m_complex` and `l_harmonic` to the input arguments
        since we cannot use `self.m_complex`.
        """
        if lmax == -1:
            lmax = self.lmax

        indices = torch.arange(len(l_harmonic))
        # Real part
        mask_r = torch.bitwise_and(l_harmonic.le(lmax), m_complex.eq(m))
        mask_idx_r = torch.masked_select(indices, mask_r)

        mask_idx_i = torch.tensor([]).long()
        # Imaginary part
        if m != 0:
            mask_i = torch.bitwise_and(l_harmonic.le(lmax), m_complex.eq(-m))
            mask_idx_i = torch.masked_select(indices, mask_i)

        return mask_idx_r, mask_idx_i

    def pre_compute_coefficient_idx(self):
        """
        Pre-compute the results of `coefficient_idx()` and access them with `prepare_coefficient_idx()`
        """
        lmax = self.lmax
        for l in range(lmax + 1):
            for m in range(lmax + 1):
                mask = torch.bitwise_and(self.l_harmonic.le(l), self.m_harmonic.le(m))
                indices = torch.arange(len(mask))
                mask_indices = torch.masked_select(indices, mask)
                self.register_buffer(
                    f"coefficient_idx_l{l}_m{m}", mask_indices, persistent=False
                )

    def prepare_coefficient_idx(self):
        """
        Construct a list of buffers
        """
        lmax = self.lmax
        coefficient_idx_list = []
        for l in range(lmax + 1):
            l_list = []
            for m in range(lmax + 1):
                l_list.append(getattr(self, f"coefficient_idx_l{l}_m{m}", None))
            coefficient_idx_list.append(l_list)
        return coefficient_idx_list

    # Return mask containing coefficients less than or equal to degree (l) and order (m)
    def coefficient_idx(self, lmax: int, mmax: int):
        if lmax > self.lmax or mmax > self.lmax:
            mask = torch.bitwise_and(self.l_harmonic.le(lmax), self.m_harmonic.le(mmax))
            indices = torch.arange(len(mask), device=mask.device)
            return torch.masked_select(indices, mask)
        else:
            temp = self.prepare_coefficient_idx()
            return temp[lmax][mmax]

    def pre_compute_rotate_inv_rescale(self):
        lmax = self.lmax
        for l in range(lmax + 1):
            for m in range(lmax + 1):
                mask_indices = self.coefficient_idx(l, m)
                rotate_inv_rescale = torch.ones(
                    (1, int((l + 1) ** 2), int((l + 1) ** 2))
                )
                for l_sub in range(l + 1):
                    if l_sub <= m:
                        continue
                    start_idx = l_sub**2
                    length = 2 * l_sub + 1
                    rescale_factor = math.sqrt(length / (2 * m + 1))
                    rotate_inv_rescale[
                        :,
                        start_idx : (start_idx + length),
                        start_idx : (start_idx + length),
                    ] = rescale_factor
                rotate_inv_rescale = rotate_inv_rescale[:, :, mask_indices]
                self.register_buffer(
                    f"rotate_inv_rescale_l{l}_m{m}",
                    rotate_inv_rescale,
                    persistent=False,
                )

    def __repr__(self):
        return f"{self.__class__.__name__}(lmax={self.lmax}, mmax={self.mmax})"


class SO3_Grid(torch.nn.Module):
    """
    Helper functions for grid representation of the irreps

    Args:
        lmax (int):   Maximum degree of the spherical harmonics
        mmax (int):   Maximum order of the spherical harmonics
    """

    def __init__(
        self,
        lmax: int,
        mmax: int,
        normalization: str = "integral",
        resolution: int | None = None,
        rescale: bool = True,
    ):
        super().__init__()
        self.lmax = lmax
        self.mmax = mmax
        self.lat_resolution = 2 * (self.lmax + 1)
        if lmax == mmax:
            self.long_resolution = 2 * (self.mmax + 1) + 1
        else:
            self.long_resolution = 2 * (self.mmax) + 1
        if resolution is not None:
            self.lat_resolution = resolution
            self.long_resolution = resolution

        self.mapping = CoefficientMapping(self.lmax, self.lmax)
        self.rescale = rescale

        to_grid = ToS2Grid(
            self.lmax,
            (self.lat_resolution, self.long_resolution),
            normalization=normalization,  # normalization="integral",
        )
        to_grid_mat = torch.einsum("mbi, am -> bai", to_grid.shb, to_grid.sha).detach()
        # rescale based on mmax
        if rescale and lmax != mmax:
            for lval in range(lmax + 1):
                if lval <= mmax:
                    continue
                start_idx = lval**2
                length = 2 * lval + 1
                rescale_factor = math.sqrt(length / (2 * mmax + 1))
                to_grid_mat[:, :, start_idx : (start_idx + length)] = (
                    to_grid_mat[:, :, start_idx : (start_idx + length)] * rescale_factor
                )
        to_grid_mat = to_grid_mat[
            :, :, self.mapping.coefficient_idx(self.lmax, self.mmax)
        ]

        from_grid = FromS2Grid(
            (self.lat_resolution, self.long_resolution),
            self.lmax,
            normalization=normalization,  # normalization="integral",
        )
        from_grid_mat = torch.einsum(
            "am, mbi -> bai", from_grid.sha, from_grid.shb
        ).detach()
        # rescale based on mmax
        if rescale and lmax != mmax:
            for lval in range(lmax + 1):
                if lval <= mmax:
                    continue
                start_idx = lval**2
                length = 2 * lval + 1
                rescale_factor = math.sqrt(length / (2 * mmax + 1))
                from_grid_mat[:, :, start_idx : (start_idx + length)] = (
                    from_grid_mat[:, :, start_idx : (start_idx + length)]
                    * rescale_factor
                )
        from_grid_mat = from_grid_mat[
            :, :, self.mapping.coefficient_idx(self.lmax, self.mmax)
        ]

        # save tensors and they will be moved to GPU
        self.register_buffer("to_grid_mat", to_grid_mat, persistent=False)
        self.register_buffer("from_grid_mat", from_grid_mat, persistent=False)

    # Compute matrices to transform irreps to grid
    def get_to_grid_mat(self, device=None):
        return self.to_grid_mat

    # Compute matrices to transform grid to irreps
    def get_from_grid_mat(self, device=None):
        return self.from_grid_mat

    # Compute grid from irreps representation
    def to_grid(self, embedding, lmax: int, mmax: int):
        to_grid_mat = self.to_grid_mat[:, :, self.mapping.coefficient_idx(lmax, mmax)]
        return torch.einsum("bai, zic -> zbac", to_grid_mat, embedding)

    # Compute irreps from grid representation
    def from_grid(self, grid, lmax: int, mmax: int):
        from_grid_mat = self.from_grid_mat[
            :, :, self.mapping.coefficient_idx(lmax, mmax)
        ]
        return torch.einsum("bai, zbac -> zic", from_grid_mat, grid)
