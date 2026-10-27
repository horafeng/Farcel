model FarcelAlgebraicGain
  parameter Real K = 2.0;
  input Real u;
  output Real y;
equation
  y = K * u;
end FarcelAlgebraicGain;
