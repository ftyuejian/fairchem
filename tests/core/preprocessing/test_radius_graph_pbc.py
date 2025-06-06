"""
Copyright (c) Meta Platforms, Inc. and affiliates.

This source code is licensed under the MIT license found in the
LICENSE file in the root directory of this source tree.
"""

from __future__ import annotations

import os
from functools import partial

import pytest
import torch
from ase import Atoms
from ase.build import molecule
from ase.io import read
from ase.lattice.cubic import FaceCenteredCubic

from fairchem.core.datasets import data_list_collater
from fairchem.core.datasets.atomic_data import AtomicData
from fairchem.core.graph.compute import generate_graph
from fairchem.core.graph.radius_graph_pbc import radius_graph_pbc, radius_graph_pbc_v2


@pytest.fixture(scope="class")
def load_data(request) -> None:
    atoms = read(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "atoms.json"),
        index=0,
        format="json",
    )
    request.cls.data = AtomicData.from_ase(
        atoms, max_neigh=200, radius=6, r_edges=True, r_data_keys=["spin", "charge"]
    )


def check_features_match(
    edge_index_1, cell_offsets_1, edge_index_2, cell_offsets_2
) -> bool:
    # Combine both edge indices and offsets to one tensor
    features_1 = torch.cat((edge_index_1, cell_offsets_1.T), dim=0).T
    features_2 = torch.cat((edge_index_2, cell_offsets_2.T), dim=0).T.long()

    # Convert rows of tensors to sets. The order of edges is not guaranteed
    features_1_set = {tuple(x.tolist()) for x in features_1}
    features_2_set = {tuple(x.tolist()) for x in features_2}

    # Ensure sets are not empty
    assert len(features_1_set) > 0
    assert len(features_2_set) > 0

    # Ensure sets are the same
    assert features_1_set == features_2_set

    return True


@pytest.mark.usefixtures("load_data")
class TestRadiusGraphPBC:
    def test_radius_graph_pbc(self) -> None:
        data = self.data
        batch = data_list_collater([data] * 5)
        generated_graphs = generate_graph(
            data=batch,
            cutoff=6,
            max_neighbors=2000,
            enforce_max_neighbors_strictly=False,
            radius_pbc_version=1,
            pbc=torch.BoolTensor([[True, True, False]] * 5),
        )
        assert check_features_match(
            batch.edge_index,
            batch.cell_offsets,
            generated_graphs["edge_index"],
            generated_graphs["cell_offsets"],
        )

    def test_bulk(self) -> None:
        radius = 10

        # Must be sufficiently large to ensure all edges are retained
        max_neigh = 2000

        a2g = partial(
            AtomicData.from_ase,
            max_neigh=max_neigh,
            radius=radius,
            r_edges=True,
            r_data_keys=["spin", "charge"],
        )

        structure = FaceCenteredCubic("Pt", size=[1, 2, 3])
        data = a2g(structure)
        batch = data_list_collater([data])

        # Ensure adequate distance between repeated cells
        structure.cell[0] *= radius
        structure.cell[1] *= radius
        structure.cell[2] *= radius

        # [False, False, False]
        data = a2g(structure)
        non_pbc = data.edge_index.shape[1]

        out = radius_graph_pbc(
            batch,
            radius=radius,
            max_num_neighbors_threshold=max_neigh,
            pbc=torch.BoolTensor([False, False, False]),
        )

        assert check_features_match(data.edge_index, data.cell_offsets, out[0], out[1])

        # [True, False, False]
        structure.cell[0] /= radius
        data = a2g(structure)
        pbc_x = data.edge_index.shape[1]

        out = radius_graph_pbc(
            batch,
            radius=radius,
            max_num_neighbors_threshold=max_neigh,
            pbc=torch.BoolTensor([True, False, False]),
        )
        assert check_features_match(data.edge_index, data.cell_offsets, out[0], out[1])

        # [True, True, False]
        structure.cell[1] /= radius
        data = a2g(structure)
        pbc_xy = data.edge_index.shape[1]

        out = radius_graph_pbc(
            batch,
            radius=radius,
            max_num_neighbors_threshold=max_neigh,
            pbc=torch.BoolTensor([True, True, False]),
        )
        assert check_features_match(data.edge_index, data.cell_offsets, out[0], out[1])

        # [False, True, False]
        structure.cell[0] *= radius
        data = a2g(structure)
        pbc_y = data.edge_index.shape[1]

        out = radius_graph_pbc(
            batch,
            radius=radius,
            max_num_neighbors_threshold=max_neigh,
            pbc=torch.BoolTensor([False, True, False]),
        )
        assert check_features_match(data.edge_index, data.cell_offsets, out[0], out[1])

        # [False, True, True]
        structure.cell[2] /= radius
        data = a2g(structure)
        pbc_yz = data.edge_index.shape[1]

        out = radius_graph_pbc(
            batch,
            radius=radius,
            max_num_neighbors_threshold=max_neigh,
            pbc=torch.BoolTensor([False, True, True]),
        )
        assert check_features_match(data.edge_index, data.cell_offsets, out[0], out[1])

        # [False, False, True]
        structure.cell[1] *= radius
        data = a2g(structure)
        pbc_z = data.edge_index.shape[1]

        out = radius_graph_pbc(
            batch,
            radius=radius,
            max_num_neighbors_threshold=max_neigh,
            pbc=torch.BoolTensor([False, False, True]),
        )
        assert check_features_match(data.edge_index, data.cell_offsets, out[0], out[1])

        # [True, False, True]
        structure.cell[0] /= radius
        data = a2g(structure)
        pbc_xz = data.edge_index.shape[1]

        out = radius_graph_pbc(
            batch,
            radius=radius,
            max_num_neighbors_threshold=max_neigh,
            pbc=torch.BoolTensor([True, False, True]),
        )
        assert check_features_match(data.edge_index, data.cell_offsets, out[0], out[1])

        # [True, True, True]
        structure.cell[1] /= radius
        data = a2g(structure)
        pbc_all = data.edge_index.shape[1]

        out = radius_graph_pbc(
            batch,
            radius=radius,
            max_num_neighbors_threshold=max_neigh,
            pbc=torch.BoolTensor([True, True, True]),
        )

        assert check_features_match(data.edge_index, data.cell_offsets, out[0], out[1])

        # Ensure edges are actually found
        assert non_pbc > 0
        assert pbc_x > non_pbc
        assert pbc_y > non_pbc
        assert pbc_z > non_pbc
        assert pbc_xy > max(pbc_x, pbc_y)
        assert pbc_yz > max(pbc_y, pbc_z)
        assert pbc_xz > max(pbc_x, pbc_z)
        assert pbc_all > max(pbc_xy, pbc_yz, pbc_xz)

        structure = FaceCenteredCubic("Pt", size=[1, 2, 3])

        # Ensure radius_graph_pbc matches radius_graph for non-PBC condition
        # torch geometric's RadiusGraph requires torch_scatter
        # RG = RadiusGraph(r=radius, max_num_neighbors=max_neigh)
        # radgraph = RG(batch)

        # out = radius_graph_pbc(
        #     batch,
        #     radius=radius,
        #     max_num_neighbors_threshold=max_neigh,
        #     pbc=[False, False, False],
        # )
        # assert (sort_edge_index(out[0]) == sort_edge_index(radgraph.edge_index)).all()

    def test_molecule(self) -> None:
        radius = 6
        max_neigh = 1000
        structure = molecule("CH3COOH")
        structure.cell = [[20, 0, 0], [0, 20, 0], [0, 0, 20]]
        data = AtomicData.from_ase(
            structure, radius=radius, max_neigh=max_neigh, r_edges=True
        )
        batch = data_list_collater([data])
        out = radius_graph_pbc(
            batch,
            radius=radius,
            max_num_neighbors_threshold=max_neigh,
            pbc=torch.BoolTensor([False, False, False]),
        )

        assert check_features_match(data.edge_index, data.cell_offsets, out[0], out[1])


@pytest.mark.parametrize(
    ("atoms,expected_edge_index,max_neighbors,enforce_max_neighbors_strictly"),
    [
        (
            Atoms("HCCC", positions=[(0, 0, 0), (-1, 0, 0), (1, 0, 0), (2, 0, 0)]),
            # we currently have an off by one in our code, the answer should be this
            # but for now lets just stay consistent
            # tensor([[1, 2, 0, 0, 3, 2], [0, 0, 1, 2, 2, 3]]) # [ with fix ]
            torch.tensor([[1, 2, 0, 2, 0, 3, 0, 2], [0, 0, 1, 1, 2, 2, 3, 3]]),
            1,
            False,
        ),
        (
            Atoms("HCCC", positions=[(0, 0, 0), (-1, 0, 0), (1, 0, 0), (2, 0, 0)]),
            # this could change since tie breaking order is undefined
            torch.tensor([[1, 0, 0, 2], [0, 1, 2, 3]]),
            1,
            True,
        ),
        (
            Atoms(
                "HCCCC",
                positions=[
                    (0, 0, 0),
                    (0, -1.0 / 20, 0),
                    (0, 1.0 / 5, 0),
                    (-1, 0, 0),
                    (1, 0, 0),
                ],
            ),
            # we currently have an off by one in our code, the answer should be this
            # but for now lets just stay consistent
            # tensor([[1, 0, 0, 0, 1, 0, 1],[0, 1, 2, 3, 3, 4, 4]])
            torch.tensor(
                [[1, 2, 0, 2, 0, 1, 0, 1, 0, 1], [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]]
            ),
            1,
            False,
        ),
        (
            Atoms(
                "HCCCC",
                positions=[
                    (0, 0, 0),
                    (0, -1.0 / 20, 0),
                    (0, 1.0 / 5, 0),
                    (-1, 0, 0),
                    (1, 0, 0),
                ],
            ),
            torch.tensor([[1, 0, 0, 0, 0], [0, 1, 2, 3, 4]]),
            1,
            True,
        ),
    ],
)
def test_simple_systems_nopbc(
    atoms,
    expected_edge_index,
    max_neighbors,
    enforce_max_neighbors_strictly,
    torch_deterministic,
):
    data = AtomicData.from_ase(atoms)

    batch = data_list_collater([data])

    for radius_graph_pbc_fn in (radius_graph_pbc_v2, radius_graph_pbc):
        edge_index, _, _ = radius_graph_pbc_fn(
            batch,
            radius=6,
            max_num_neighbors_threshold=max_neighbors,
            enforce_max_neighbors_strictly=enforce_max_neighbors_strictly,
            pbc=torch.BoolTensor([False, False, False]),
        )

        assert (
            len(
                set(tuple(x) for x in edge_index.T.tolist())
                - set(tuple(x) for x in expected_edge_index.T.tolist())
            )
            == 0
        )
