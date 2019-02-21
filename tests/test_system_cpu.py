import numpy as np
import os
import unittest

from timemachine.cpu_functionals import custom_ops
# OpenMM nonbonded terms
# NoCutoff = 0,
# CutoffNonPeriodic = 1,
# CutoffPeriodic = 2,
# Ewald = 3,
# PME = 4,
# LJPME = 5

import xml.etree.ElementTree as ET


def addExclusionsToSet(bonded12, exclusions, baseParticle, fromParticle, currentLevel):
    for i in bonded12[fromParticle]:
        if i != baseParticle:
            exclusions.add(i)
        if currentLevel > 0:
            addExclusionsToSet(bonded12, exclusions, baseParticle, i, currentLevel-1)

def deserialize_system(xml_file):
    """
    Deserialize an openmm XML file into a set of functional forms
    supported by the time machine.
    """
    tree = ET.parse(xml_file)
    root = tree.getroot()

    masses = []

    all_nrgs = []

    start_params = 0

    for child in root:
        if child.tag == 'Particles':
            for subchild in child:
                masses.append(np.float64(subchild.attrib['mass']))
            num_atoms = len(masses)
        if child.tag == 'Forces':
            for subchild in child:
                tags = subchild.attrib
                force_group = np.int32(tags['forceGroup'])
                force_version = np.int32(tags['version'])
                force_type = tags['type']
                if force_type == 'HarmonicBondForce':
                    force_periodic = tags['usesPeriodic']

                    params = []
                    param_idxs = []
                    bond_idxs = []

                    for bonds in subchild:
                        for bond in bonds:
                            d = np.float64(bond.attrib['d'])
                            k = np.float64(bond.attrib['k'])
                            src = np.int32(bond.attrib['p1'])
                            dst = np.int32(bond.attrib['p2'])

                            p_idx_d = len(params)
                            params.append(d)
                            p_idx_k = len(params)
                            params.append(k)

                            param_idxs.append((p_idx_k, p_idx_d))
                            bond_idxs.append((src, dst))

                    n_params = len(params)
                    params = np.array(params)
                    param_idxs = np.array(param_idxs)
                    bond_idxs = np.array(bond_idxs)

                    all_nrgs.append(custom_ops.HarmonicBondGPU_float(
                        params.astype(np.float32).reshape(-1).tolist(),
                        list(range(start_params, start_params+n_params)),
                        param_idxs.reshape(-1).tolist(),
                        bond_idxs.reshape(-1).tolist()
                    ))

                    start_params += n_params

                elif force_type == 'HarmonicAngleForce':
                    force_periodic = tags['usesPeriodic']

                    params = []
                    param_idxs = []
                    angle_idxs = []

                    for angles in subchild:
                        for angle in angles:
                            a = np.float64(angle.attrib['a'])
                            k = np.float64(angle.attrib['k'])
                            src = np.int32(angle.attrib['p1'])
                            mid = np.int32(angle.attrib['p2'])
                            dst = np.int32(angle.attrib['p3'])

                            p_idx_a = len(params)
                            params.append(a)
                            p_idx_k = len(params)
                            params.append(k)

                            param_idxs.append((p_idx_k, p_idx_a))
                            angle_idxs.append((src, mid, dst))

                    n_params = len(params)
                    params = np.array(params)
                    param_idxs = np.array(param_idxs)
                    angle_idxs = np.array(angle_idxs)

                    all_nrgs.append(custom_ops.HarmonicAngleGPU_float(
                        params.astype(np.float32).reshape(-1).tolist(),
                        list(range(start_params, start_params+n_params)),
                        param_idxs.reshape(-1).tolist(),
                        angle_idxs.reshape(-1).tolist())
                    )

                    start_params += n_params

                elif force_type == 'PeriodicTorsionForce':

                    params = []
                    param_idxs = []
                    torsion_idxs = []

                    for tors in subchild:
                        for tor in tors:
                            k = np.float64(tor.attrib['k'])
                            phase = np.float64(tor.attrib['phase'])
                            periodicity = np.float64(tor.attrib['periodicity'])
                            p1 = np.int32(tor.attrib['p1'])
                            p2 = np.int32(tor.attrib['p2'])
                            p3 = np.int32(tor.attrib['p3'])
                            p4 = np.int32(tor.attrib['p4'])

                            p_idx_k = len(params)
                            params.append(k)
                            p_idx_phase = len(params)
                            params.append(phase)
                            p_idx_period = len(params)
                            params.append(periodicity)

                            param_idxs.append((p_idx_k, p_idx_phase, p_idx_period))
                            torsion_idxs.append((p1, p2, p3, p4))

                    n_params = len(params)
                    params = np.array(params)
                    param_idxs = np.array(param_idxs)
                    torsion_idxs = np.array(torsion_idxs)

                    all_nrgs.append(custom_ops.PeriodicTorsionGPU_float(
                        params.astype(np.float32).reshape(-1).tolist(),
                        list(range(start_params, start_params+n_params)),
                        param_idxs.reshape(-1).tolist(),
                        torsion_idxs.reshape(-1).tolist()
                    ))

                    start_params += n_params

                elif force_type == 'NonbondedForce':

                    assert len(bond_idxs) > 0
                    assert len(angle_idxs) > 0
                    assert len(torsion_idxs) > 0

                    method = np.int32(tags['method'])

                    if method != 1:
                        raise TypeError('Only nonperiodic cutoff Nonbonded is supported for now.')

                    alpha = np.float64(tags['alpha'])
                    cutoff = np.float64(tags['cutoff'])
                    dispersionCorrection = np.bool(tags['dispersionCorrection'])
                    ewaldTolerance = np.float64(tags['ewaldTolerance'])
                    ljAlpha = np.float64(tags['ljAlpha'])

                    coulomb14scale = 0.833333
                    lj14scale = 0.5

                    lj_eps_scales = np.ones(shape=(num_atoms, num_atoms), dtype=np.float64)
                    charge_scales = np.ones(shape=(num_atoms, num_atoms), dtype=np.float64)

                    exclusions = []
                    bonded12 = []
                    # diagonals
                    for a_idx in range(num_atoms):
                        lj_eps_scales[a_idx][a_idx] = 0.0
                        charge_scales[a_idx][a_idx] = 0.0

                        exclusions.append(set())
                        bonded12.append(set())

                    # taken from openmm's createExceptionsFromBonds()
                    for first, second in bond_idxs:
                        bonded12[first].add(second)
                        bonded12[second].add(first)

                    for i in range(num_atoms):
                        addExclusionsToSet(bonded12, exclusions[i], i, i, 2)

                    for i in range(num_atoms):
                        bonded13 = set()
                        addExclusionsToSet(bonded12, bonded13, i, i, 1)
                        for j in exclusions[i]:
                            if j < i:
                                if j not in bonded13:
                                    lj_eps_scales[i][j] = lj14scale
                                    lj_eps_scales[j][i] = lj14scale
                                    charge_scales[i][j] = coulomb14scale
                                    charge_scales[j][i] = coulomb14scale
                                else:
                                    lj_eps_scales[i][j] = 0.0
                                    lj_eps_scales[j][i] = 0.0
                                    charge_scales[i][j] = 0.0
                                    charge_scales[j][i] = 0.0

                    charge_params = []
                    charge_param_idxs = []

                    lj_params = []
                    lj_param_idxs = []

                    for group in subchild:
                        if group.tag == 'Particles':
                            for p_idx, particle in enumerate(group):
                                q_idx = len(charge_params)
                                charge_params.append(np.float64(particle.attrib['q']))

                                sig_idx = len(lj_params)
                                lj_params.append(np.float64(particle.attrib['sig']))

                                eps_idx = len(lj_params)
                                lj_params.append(np.float64(particle.attrib['eps']))

                                charge_param_idxs.append(q_idx)
                                lj_param_idxs.append((sig_idx, eps_idx))

                    n_charge_params = len(charge_params)
                    n_lj_params = len(lj_params)

                    charge_params = np.array(charge_params)
                    charge_param_idxs = np.array(charge_param_idxs)

                    lj_params = np.array(lj_params)
                    lj_param_idxs = np.array(lj_param_idxs)

                    lj = custom_ops.LennardJonesGPU_float(
                        lj_params.astype(np.float32).reshape(-1).tolist(),
                        list(range(start_params, start_params+n_lj_params)),
                        lj_param_idxs.reshape(-1).tolist(),
                        lj_eps_scales.reshape(-1).tolist()
                    )
                    start_params += n_lj_params

                    es = custom_ops.ElectrostaticsGPU_float(
                        charge_params.astype(np.float32).reshape(-1).tolist(),
                        list(range(start_params, start_params+n_charge_params)),
                        charge_param_idxs.reshape(-1).tolist(),
                        charge_scales.reshape(-1).tolist()
                    )
                    start_params += n_charge_params

                    all_nrgs.extend([lj, es])

                elif force_type == 'GBSAOBCForce':

                    # GBSA not implemented yet.
                    continue

                    method = np.int32(tags['method'])

                    if method != 1:
                        raise TypeError('Only nonperiodic cutoff GBSA is supported for now.')

                    soluteDielectric = np.float64(tags['soluteDielectric'])
                    solventDielectric = np.float64(tags['solventDielectric'])
                    surfaceAreaEnergy = np.float64(tags['surfaceAreaEnergy'])
                    cutoff = np.float64(tags['cutoff'])

                    # (ytz): infer coloumb 1-4 scale and lj 1-4 scale
                    coloumb14Scale = 0.833333
                    lj14scale = 0.5

                    params = []
                    param_idxs = []

                    for particles in subchild:
                        for particle in particles:
                            q_idx = len(params)
                            params.append(np.float64(particle.attrib['q']))
                            r_idx = len(params)
                            params.append(np.float64(particle.attrib['r']))
                            s_idx = len(params)
                            params.append(np.float64(particle.attrib['scale']))

                            param_idxs.append((q_idx, r_idx, s_idx))

                    params = np.array(params)
                    param_idxs = np.array(param_idxs)

                    all_nrgs.append(gbsa.GBSAOBC(
                        params,
                        param_idxs,
                        cutoff=cutoff,
                        soluteDielectric=soluteDielectric,
                        solventDielectric=solventDielectric,
                        surfaceAreaEnergy=surfaceAreaEnergy))


    return masses, all_nrgs, start_params

def deserialize_state(xml_file):

    tree = ET.parse(xml_file)
    root = tree.getroot()

    pot_nrg = None
    coords = []
    velocities = []
    forces = []

    for child in root:
        if child.tag == 'Energies':
            pot_nrg = np.float64(child.attrib['PotentialEnergy'])
        elif child.tag == 'Positions':
            for subchild in child:
                x, y, z = np.float64(subchild.attrib['x']), np.float64(subchild.attrib['y']), np.float64(subchild.attrib['z'])
                coords.append((x,y,z))
        elif child.tag == 'Velocities':
            for subchild in child:
                x, y, z = np.float64(subchild.attrib['x']), np.float64(subchild.attrib['y']), np.float64(subchild.attrib['z'])
                velocities.append((x,y,z))
        elif child.tag == 'Forces':
            for subchild in child:
                x, y, z = np.float64(subchild.attrib['x']), np.float64(subchild.attrib['y']), np.float64(subchild.attrib['z'])
                forces.append((x,y,z))

    return pot_nrg, np.array(coords), np.array(velocities), np.array(forces)


def get_data(fname):
    return os.path.join(os.path.dirname(__file__), 'data', fname)


class TestSpeed(unittest.TestCase):

    def test_ala(self):

        masses, nrgs, n_params = deserialize_system('/home/yutong/Code/timemachine/system.xml')
        ref_nrg, x0, velocities, ref_forces = deserialize_state('/home/yutong/Code/timemachine/state0.xml')

        num_atoms = x0.shape[0]

        dxdp = np.random.rand(n_params, num_atoms, 3)
        print("dxdp shape", dxdp.shape)

        for nrg in nrgs:
            print(nrg)
            e, grad, hess, mixed_partials = nrg.total_derivative(x0.astype(np.float32), n_params)
            print(e)
        return
        assert 0


        # coeff_a = np.float32(0.5)
        # coeff_bs = np.expand_dims(np.array(masses), axis=1)
        # coeff_cs = np.expand_dims(1/np.array(masses), axis=1)
        # buffer_size = 100
        # dt = 0.03


        # gpu_intg = custom_ops.Integrator_float(
        #     dt,
        #     buffer_size,
        #     num_atoms,
        #     num_params,
        #     coeff_a,
        #     coeff_bs.reshape(-1).tolist(),
        #     coeff_cs.reshape(-1).tolist()
        # )

        nrg_op = nrgs[0].energy(x_ph)
        grad_op = densify(tf.gradients(nrg_op, x_ph)[0])
        nrg_val, grad_val = sess.run([nrg_op, grad_op], feed_dict={x_ph: x0})
        np.testing.assert_almost_equal(ref_nrg, nrg_val)
        np.testing.assert_almost_equal(ref_forces, grad_val*-1)

        # angles
        ref_nrg, x0, velocities, ref_forces = deserialize_state(get_data('state1.xml'))
        nrg_op = nrgs[1].energy(x_ph)
        grad_op = densify(tf.gradients(nrg_op, x_ph)[0])
        nrg_val, grad_val = sess.run([nrg_op, grad_op], feed_dict={x_ph: x0})
        np.testing.assert_almost_equal(ref_nrg, nrg_val)
        np.testing.assert_almost_equal(ref_forces, grad_val*-1)

        # torsion
        ref_nrg, x0, velocities, ref_forces = deserialize_state(get_data('state2.xml'))
        nrg_op = nrgs[2].energy(x_ph)
        grad_op = densify(tf.gradients(nrg_op, x_ph)[0])
        nrg_val, grad_val = sess.run([nrg_op, grad_op], feed_dict={x_ph: x0})
        np.testing.assert_almost_equal(ref_nrg, nrg_val)
        np.testing.assert_almost_equal(ref_forces, grad_val*-1)

        # nonbonded
        ref_nrg, x0, velocities, ref_forces = deserialize_state(get_data('state3.xml'))
        lj_nrg_op = nrgs[3].energy(x_ph)
        es_nrg_op = nrgs[4].energy(x_ph)
        lj_grad_op = densify(tf.gradients(lj_nrg_op, x_ph)[0])
        es_grad_op = densify(tf.gradients(es_nrg_op, x_ph)[0])
        lj_nrg_val, lj_grad_val, es_nrg_val, es_grad_val = sess.run([lj_nrg_op, lj_grad_op, es_nrg_op, es_grad_op], feed_dict={x_ph: x0})
        tot_e = lj_nrg_val + es_nrg_val
        np.testing.assert_almost_equal(ref_nrg, tot_e)
        grad_val = lj_grad_val + es_grad_val
        np.testing.assert_almost_equal(ref_forces, grad_val*-1)

        # GBSA
        # ref_nrg, x0, velocities, ref_forces = deserialize_state(get_data('state4.xml'))
        # nrg_op = nrgs[5].energy(x_ph)
        # grad_op = densify(tf.gradients(nrg_op, x_ph)[0])
        # nrg_val, grad_val = sess.run([nrg_op, grad_op], feed_dict={x_ph: x0})
        # np.testing.assert_almost_equal(ref_nrg, nrg_val)
        # np.testing.assert_almost_equal(ref_forces, grad_val*-1)



if __name__ == "__main__":
    unittest.main()
        
