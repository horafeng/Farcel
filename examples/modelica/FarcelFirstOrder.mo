model FarcelFirstOrder
  parameter Real T(min=1e-9) = 0.5;
  input Real u;
  output Real y(start=0, fixed=true);
equation
  der(y) = (u - y) / T;
end FarcelFirstOrder;
