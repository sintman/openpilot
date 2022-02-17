import math
import numpy as np

from selfdrive.controls.lib.drive_helpers import get_steer_max
from cereal import log
from selfdrive.controls.lib.latcontrol import LatControl, MIN_STEER_SPEED
from common.realtime import DT_CTRL

NX = 2

controls_list = ['can_cmds', 'can_cmds_squared', 'cmds_by_vsquared']
NU = len(controls_list)

live_param_list = ['roll', 'speed_times_roll', 'roll_squared', 'roll_by_speed']
N_live_param = len(live_param_list)


class LatControlSteerModel(LatControl):
  def __init__(self, CP, CI):
    super().__init__(CP, CI)

    self.xcurrent = np.zeros((NX, ))

    model_param = np.asarray(list(CP.lateralTuning.steerModel.modelparam))

    self.A = model_param[0:NX*NX].reshape((NX, NX), order='F')
    self.B = model_param[NX*NX:NX*(NX+NU)].reshape((NX, NU), order='F')
    self.R = model_param[NX*(NX+NU):].reshape((NX, N_live_param), order='F')

    # print(f"LatControlSteerModel: \nA {self.A}\nB {self.B}\nR {self.R}\n")

    self.W = np.diag([1e0, 1e-1])
    # self.PHI = (DT_CTRL * self.B).reshape((2,1))
    # self.M_tilde = - 1/(self.PHI.T @ self.W @ self.PHI) * (self.PHI.T@self.W)
    self.torque = 0.0

  def reset(self):
    # when is this called? only in if below?
    super().reset()
    self.xcurrent = np.zeros((NX, ))

  def update(self, active, CS, CP, VM, params, last_actuators, desired_curvature, desired_curvature_rate):
    model_log = log.ControlsState.LateralSteerModelState.new_message()

    steers_max = get_steer_max(CP, CS.vEgo)

    # offset does not contribute to resistive torque
    angle_steers_des_no_offset = math.degrees(VM.get_steer_from_curvature(-desired_curvature, CS.vEgo, params.roll))
    angle_steers_des = angle_steers_des_no_offset + params.angleOffsetDeg

    model_log.steeringAngleDesiredDeg = angle_steers_des

    if CS.vEgo < MIN_STEER_SPEED or not active:
      output_steer = 0.0
      model_log.active = False
      self.reset()
    else:
      # NOTE: live_param_list dependent.
      # live_param_list = ['roll', 'speed_times_roll', 'roll_squared', 'roll_by_speed'] #, 'speed_times_angle']
      roll_deg = np.degrees(params.roll)
      live_param = np.array([roll_deg, CS.vEgo * roll_deg, roll_deg * abs(roll_deg), roll_deg / CS.vEgo])
      Rp = self.R @ live_param

      # NOTE: controls_list dependent.
      u = np.array([last_actuators.steer, last_actuators.steer*abs(last_actuators.steer), last_actuators.steer/(CS.vEgo**2)])
      # update state estimate with forward simulation
      self.xcurrent = self.xcurrent + DT_CTRL * (self.A @ self.xcurrent + Rp + (self.B @ u))

      # analytical solution similar to steady state.
      # steady_state_torque = self.M * (self.A @ self.xcurrent + self.R @ live_param) # solve for xdot = 0

      # torque = argmin norm([desired_angle, xcurrent_1] + DT_CTRL * (A*xcurrent + R*live_param + B*u))
      # torque_np = self.M @ (self.A @ self.xcurrent + self.R @ live_param +
                # (self.xcurrent - np.array([angle_steers_des_no_offset, self.xcurrent[1,]]))/DT_CTRL )
      #
      desired_angle_rate = math.degrees(VM.get_steer_from_curvature(-desired_curvature_rate, CS.vEgo, params.roll))

      # torque = argmin norm(xcurrent + DT_CTRL * (A*xcurrent + R*live_param + B*u) - [desired_angle, desired_angle_rate])_W
      # NOTE: controls_list dependent.
      du_dtorque = np.array([1, 2*np.abs(self.torque), 1/(CS.vEgo**2)])
      B_tilde = self.B @ du_dtorque
      Phi = (DT_CTRL * B_tilde).reshape((2,1))
      M_tilde = - 1/(Phi.T @ self.W @ Phi) * (Phi.T@self.W)

      torque_np = M_tilde @ (self.xcurrent + DT_CTRL * (self.A @ self.xcurrent + Rp) -
                      np.array([angle_steers_des_no_offset, desired_angle_rate])).reshape((2,1))

      # hacky but works well..
      # B_tilde = B_tilde.reshape((2,1))
      # M = - np.linalg.solve(B_tilde.T @ B_tilde, B_tilde.T)
      # torque_np = M @ (self.A @ self.xcurrent + self.R @ live_param +
      #           (self.xcurrent - np.array([angle_steers_des_no_offset, .95*self.xcurrent[1,]]))/DT_CTRL )
      output_steer = float(torque_np)
      self.torque = output_steer
      model_log.active = True

    model_log.steeringAngleDeg = float(self.xcurrent[0])
    model_log.output = output_steer
    model_log.saturated = self._check_saturation(steers_max - abs(output_steer) < 1e-3, CS)

    return output_steer, angle_steers_des, model_log
