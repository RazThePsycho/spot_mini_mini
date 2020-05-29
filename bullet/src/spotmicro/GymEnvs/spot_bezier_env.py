""" This file implements the gym environment of SpotMicro with Bezier Curve.
"""
import math
import time
import gym
import numpy as np
import pybullet
import pybullet_data
from gym import spaces
from gym.utils import seeding
from pkg_resources import parse_version
from spotmicro import spot
import pybullet_utils.bullet_client as bullet_client
from gym.envs.registration import register
from spotmicro.OpenLoopSM.SpotOL import BezierStepper
from spotmicro.spot_gym_env import spotGymEnv
import spotmicro.Kinematics.LieAlgebra as LA

SENSOR_NOISE_STDDEV = spot.SENSOR_NOISE_STDDEV

# Register as OpenAI Gym Environment
register(
    id="SpotMicroEnv-v1",
    entry_point='spotmicro.GymEnvs.spot_bezier_env:spotBezierEnv',
    max_episode_steps=1000,
)


class spotBezierEnv(spotGymEnv):
    """The gym environment for spot.

  It simulates the locomotion of spot, a quadruped robot. The state space
  include the angles, velocities and torques for all the motors and the action
  space is the desired motor angle for each motor. The reward function is based
  on how far spot walks in 1000 steps and penalizes the energy
  expenditure.

  """
    metadata = {
        "render.modes": ["human", "rgb_array"],
        "video.frames_per_second": 50
    }

    def __init__(self,
                 distance_weight=1.0,
                 rotation_weight=1.0,
                 energy_weight=0.0005,
                 shake_weight=0.005,
                 drift_weight=2.0,
                 rp_weight=0.1,
                 rate_weight=0.1,
                 urdf_root=pybullet_data.getDataPath(),
                 urdf_version=None,
                 distance_limit=float("inf"),
                 observation_noise_stdev=SENSOR_NOISE_STDDEV,
                 self_collision_enabled=True,
                 motor_velocity_limit=np.inf,
                 pd_control_enabled=False,
                 leg_model_enabled=False,
                 accurate_motor_model_enabled=False,
                 remove_default_joint_damping=False,
                 motor_kp=2.0,
                 motor_kd=0.03,
                 control_latency=0.0,
                 pd_latency=0.0,
                 torque_control_enabled=False,
                 motor_overheat_protection=False,
                 hard_reset=False,
                 on_rack=False,
                 render=True,
                 num_steps_to_log=1000,
                 action_repeat=1,
                 control_time_step=None,
                 env_randomizer=None,
                 forward_reward_cap=float("inf"),
                 reflection=True,
                 log_path=None,
                 desired_velocity=0.5,
                 desired_rate=0.0,
                 lateral=False,
                 draw_foot_path=False,
                 height_field=False,
                 AutoStepper=True):

        super(spotBezierEnv, self).__init__(
            distance_weight=distance_weight,
            rotation_weight=rotation_weight,
            energy_weight=energy_weight,
            shake_weight=shake_weight,
            drift_weight=drift_weight,
            rp_weight=rp_weight,
            rate_weight=rate_weight,
            urdf_root=urdf_root,
            urdf_version=urdf_version,
            distance_limit=distance_limit,
            observation_noise_stdev=observation_noise_stdev,
            self_collision_enabled=self_collision_enabled,
            motor_velocity_limit=motor_velocity_limit,
            pd_control_enabled=pd_control_enabled,
            leg_model_enabled=leg_model_enabled,
            accurate_motor_model_enabled=accurate_motor_model_enabled,
            remove_default_joint_damping=remove_default_joint_damping,
            motor_kp=motor_kp,
            motor_kd=motor_kd,
            control_latency=control_latency,
            pd_latency=pd_latency,
            torque_control_enabled=torque_control_enabled,
            motor_overheat_protection=motor_overheat_protection,
            hard_reset=hard_reset,
            on_rack=on_rack,
            render=render,
            num_steps_to_log=num_steps_to_log,
            action_repeat=action_repeat,
            control_time_step=control_time_step,
            env_randomizer=env_randomizer,
            forward_reward_cap=forward_reward_cap,
            reflection=reflection,
            log_path=log_path,
            desired_velocity=desired_velocity,
            desired_rate=desired_rate,
            lateral=lateral,
            draw_foot_path=draw_foot_path,
            height_field=height_field,
            AutoStepper=AutoStepper)

    def step(self, action, smach):
        """Step forward the simulation, given the action.

    Args:
      action: A list of desired motor angles for eight motors.
      smach: the bezier state machine containing simulated
      		 random controll inputs

    Returns:
      observations: The angles, velocities and torques of all motors.
      reward: The reward for the current state-action pair.
      done: Whether the episode has ended.
      info: A dictionary that stores diagnostic information.

    Raises:
      ValueError: The action dimension is not the same as the number of motors.
      ValueError: The magnitude of actions is out of bounds.
    """
        self._last_base_position = self.spot.GetBasePosition()
        self._last_base_orientation = self.spot.GetBaseOrientation()
        # print("ACTION:")
        # print(action)
        if self._is_render:
            # Sleep, otherwise the computation takes less time than real time,
            # which will make the visualization like a fast-forward video.
            time_spent = time.time() - self._last_frame_time
            self._last_frame_time = time.time()
            time_to_sleep = self.control_time_step - time_spent
            if time_to_sleep > 0:
                time.sleep(time_to_sleep)
            base_pos = self.spot.GetBasePosition()
            # Keep the previous orientation of the camera set by the user.
            [yaw, pitch,
             dist] = self._pybullet_client.getDebugVisualizerCamera()[8:11]
            self._pybullet_client.resetDebugVisualizerCamera(
                dist, yaw, pitch, base_pos)

        action = self._transform_action_to_motor_command(action)
        self.spot.Step(action)
        # NOTE: SMACH is passed to the reward method
        reward = self._reward(smach)
        done = self._termination()
        self._env_step_counter += 1

        # DRAW FOOT PATH
        if self.draw_foot_path:
            self.DrawFootPath()
        return np.array(self._get_observation()), reward, done, {}

    def _reward(self, smach):
        # Return simulated controller values for reward calc
        _, _, StepLength, LateralFraction, YawRate, StepVelocity, _, _ = smach.return_bezier_params(
        )

        # Return StepVelocity with the sign of StepLength
        DesiredVelicty = math.copysign(StepVelocity / 4.0, StepLength)

        # GETTING TWIST IN BODY FRAME
        pos = self.spot.GetBasePosition()
        orn = self.spot.GetBaseOrientation()
        roll, pitch, yaw = self._pybullet_client.getEulerFromQuaternion(
            [orn[0], orn[1], orn[2], orn[3]])
        rpy = LA.RPY(roll, pitch, yaw)
        R, _ = LA.TransToRp(rpy)
        T_wb = LA.RpToTrans(R, np.array([pos[0], pos[1], pos[2]]))
        T_bw = LA.TransInv(T_wb)
        Adj_Tbw = LA.Adjoint(T_bw)

        Vw = np.concatenate(
            (self.spot.prev_ang_twist, self.spot.prev_lin_twist))
        Vb = np.dot(Adj_Tbw, Vw)

        # New Twist in Body Frame

        # get observation
        obs = self._get_observation()

        # POSITIVE FOR FORWARD, NEGATIVE FOR BACKWARD | NOTE: HIDDEN
        fwd_speed = -Vb[3]  # vx
        lat_speed = -Vb[4]  # vy

        # Modification for lateral/fwd rewards
        reward_max = 1.0
        # FORWARD/LATERAL
        forward_reward = reward_max * np.exp(
            -(fwd_speed - DesiredVelicty * np.cos(LateralFraction))**2 / (0.1))
        lateral_reward = reward_max * np.exp(
            -(lat_speed - DesiredVelicty * np.sin(LateralFraction))**2 / (0.1))

        # print("FWD SPEED: {:.2f} \t DESIRED: {:.2f} ".format(
        #     fwd_speed, DesiredVelicty * np.cos(LateralFraction)))

        # print("LAT SPEED: {:.2f} \t DESIRED: {:.2f} ".format(
        #     lat_speed, DesiredVelicty * np.sin(LateralFraction)))

        # print("-----------------------------------------------")

        # print("YAW: {}".format(current_yaw))
        # print("SIN YAW: {}".format(np.sin(current_yaw)))
        # print("COS YAW: {}".format(np.cos(current_yaw)))

        forward_reward += lateral_reward

        yaw_rate = obs[4]

        rot_reward = reward_max * np.exp(-(yaw_rate - YawRate)**2 / (0.1))

        # penalty for nonzero roll, pitch
        rp_reward = -(abs(obs[0]) + abs(obs[1]))
        # print("ROLL: {} \t PITCH: {}".format(obs[0], obs[1]))

        # penalty for nonzero acc(z) - UNRELIABLE ON IMU
        shake_reward = 0

        # penalty for nonzero rate (x,y,z)
        rate_reward = -(abs(obs[2]) + abs(obs[3]))

        drift_reward = 0
        energy_reward = -np.abs(
            np.dot(self.spot.GetMotorTorques(),
                   self.spot.GetMotorVelocities())) * self._time_step
        reward = (self._distance_weight * forward_reward +
                  self._rotation_weight * rot_reward +
                  self._energy_weight * energy_reward +
                  self._drift_weight * drift_reward +
                  self._shake_weight * shake_reward +
                  self._rp_weight * rp_reward +
                  self._rate_weight * rate_reward)
        self._objectives.append(
            [forward_reward, energy_reward, drift_reward, shake_reward])
        # print("REWARD: ", reward)
        return reward