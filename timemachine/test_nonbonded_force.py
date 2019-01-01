import unittest
import numpy as np
import tensorflow as tf
from timemachine.nonbonded_force import ElectrostaticForce
from timemachine.constants import ONE_4PI_EPS0

class TestNonbondedForce(unittest.TestCase):

    def tearDown(self):
        tf.reset_default_graph()

    def test_electrostatics(self):
        """
        Testing non-periodic electrostatic forces.
        """
        x0 = np.array([
            [ 0.0637,   0.0126,   0.2203],
            [ 1.0573,  -0.2011,   1.2864],
            [ 2.3928,   1.2209,  -0.2230],
            [-0.6891,   1.6983,   0.0780],
            [-0.6312,  -1.6261,  -0.2601]
        ], dtype=np.float64)

        x_ph = tf.placeholder(shape=(5, 3), dtype=np.float64)

        params = np.array([1.3, 0.3], dtype=np.float64)
        param_idxs = np.array([0, 1, 1, 1, 1], dtype=np.int32)
        exclusions = np.array([
            [0,0,1,0,0],
            [0,0,0,1,1],
            [1,0,0,0,0],
            [0,1,0,0,1],
            [0,1,0,1,0],
            ], dtype=np.bool)

        ef = ElectrostaticForce(params, param_idxs, exclusions)

        sess = tf.Session()
        sess.run(tf.initializers.global_variables())

        num_atoms = 5
        ref_nrg = 0

        for i in range(num_atoms):
            qi = params[param_idxs[i]]
            for j in range(i+1, num_atoms):
                if not exclusions[i, j]:
                    qj = params[param_idxs[j]]
                    qij = qi * qj
                    dij = np.linalg.norm(x0[i] - x0[j])
                    ref_nrg += qij/dij

        ref_nrg *= ONE_4PI_EPS0

        # (ytz) TODO: add a test for forces, for now we rely on
        # autograd to be analytically correct.
        test_nrg_op = ef.energy(x_ph)

        test_nrg = sess.run(test_nrg_op, feed_dict={x_ph: x0})
        np.testing.assert_almost_equal(ref_nrg, test_nrg)

        test_grads_op = ef.gradients(x_ph)
        test_grads = sess.run(test_grads_op, feed_dict={x_ph: x0})

        net_force = np.sum(test_grads, axis=0)
        # this also checks that there are no NaNs
        np.testing.assert_almost_equal(net_force, [0,0,0], decimal=14)



if __name__ == "__main__":
    unittest.main()